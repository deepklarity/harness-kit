#!/usr/bin/env python3
"""Reflection reviewer model evaluator — measure parse %, verdict agreement, ERROR %.

The board's `reflection_model` is currently pinned to claude-sonnet-5 because
haiku burned rework cycles emitting unparseable multi-section markdown
(task 159). The fenced-JSON contract + lenient parser + hard-ERROR shipped
in wave 3 (`odin/src/odin/reflection.py`), but nobody has measured whether a
cheap model can now emit the contract reliably. This harness answers that
question with numbers, not vibes.

Two operating modes:

  1. LIVE — call `claude -p <prompt> --model <model>` via subprocess for each
     (input, model) pair, capture the raw stdout, and parse it with the
     current parser. Mirrors the production `reflect_task()` path:
       * `--bare --setting-sources user` to skip the workspace-trust warning
       * `--output-format stream-json` so the harness can extract text cleanly
       * timeout = 600s (reflection-timeout production default is 1800s;
         600s keeps the eval loop tight)
     Use this when ANTHROPIC_API_KEY (or logged-in OAuth) is available.

  2. OFFLINE — replay captured reflection outputs from
     `fixtures/inputs/<id>/<model>.raw.txt` through the same parser. Use
     this when no API is available (sandboxed task worktrees); the metrics
     describe parser behaviour on the historical captures, not model
     quality in general.

Both modes produce the same table format:

  model   N   parse%   agreement%   ERROR%

Acceptance criteria (per task 191):
  - Harness runs end-to-end and produces the table.
  - Recommendation stated with evidence.
  - No board settings changed.

Recommendation surface (CLI):
  --recommend  Print a one-line recommendation: flip / keep / size-scale.

Why not the existing pytest suite? The reflection test suite pins
PARSER behaviour. This harness pins MODEL behaviour by running the
reviewer prompt against candidate models and observing what comes back.
Different scope, different artifact.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from odin.reflection import parse_reflection_report

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
INPUTS_DIR = FIXTURES_DIR / "inputs"

# Allowed verdict values from the parser.
ENUM_VERDICTS = ("PASS", "NEEDS_WORK", "FAIL")
ERROR_VERDICT = "ERROR"

# Time-bounded subprocess run for the live path. Reflection timeout in
# production is 1800s (DAG_EXECUTOR_REFLECTION_TIMEOUT_SECONDS); 600s
# keeps a measurement loop practical — if a candidate model can't emit
# the contract in 10 minutes, the marginal value of giving it more time
# is small (cheap models stay cheap; long-running cheap is just long).
LIVE_TIMEOUT_SECONDS = 600

# Strip the Claude CLI stream-json envelope so the raw_output that hits the
# parser looks like what `harness.execute()` would have tee'd into the
# trace file. Mirrors `odin.harnesses.base.extract_text_from_stream`.
_STREAM_JSON_PREFIX = re.compile(r"^\s*\{.*?\}\s*$", re.MULTILINE)


@dataclass
class InputFixture:
    """One captured (prompt, ground-truth) reflection input.

    `prompt_file` holds the assembled reflection prompt that was (or would
    be) sent to the reviewer. `ground_truth` is the authoritative verdict
    a human reviewer (or replay-table operator) gave for this input —
    agreement% compares each model's parse against this ground truth.
    """

    input_id: str
    prompt_file: Path
    ground_truth: dict | None = None
    captures: dict[str, Path] = field(default_factory=dict)

    @property
    def ground_truth_verdict(self) -> str | None:
        return self.ground_truth.get("verdict") if self.ground_truth else None


@dataclass
class ModelRun:
    """One (input × model) measurement row."""

    input_id: str
    model: str
    verdict: str
    parseable: bool
    is_error: bool
    has_quality: bool
    has_slop: bool
    has_improvements: bool
    has_fix_list: bool
    has_summary: bool
    verdict_summary: str
    raw_chars: int
    agreement: bool | None  # None if no ground truth
    elapsed_s: float = 0.0
    raw_output_path: str | None = None

    def to_dict(self) -> dict:
        return {
            "input_id": self.input_id,
            "model": self.model,
            "verdict": self.verdict,
            "parseable": self.parseable,
            "is_error": self.is_error,
            "has_quality": self.has_quality,
            "has_slop": self.has_slop,
            "has_improvements": self.has_improvements,
            "has_fix_list": self.has_fix_list,
            "has_summary": self.has_summary,
            "verdict_summary": self.verdict_summary[:200],
            "raw_chars": self.raw_chars,
            "agreement": self.agreement,
            "elapsed_s": round(self.elapsed_s, 2),
            "raw_output_path": self.raw_output_path,
        }


def _load_input(input_dir: Path) -> InputFixture | None:
    """Load one input directory. Returns None if it lacks a prompt file."""
    prompt_file = input_dir / "prompt.txt"
    if not prompt_file.exists():
        return None
    ground_truth = None
    gt_file = input_dir / "ground_truth.json"
    if gt_file.exists():
        ground_truth = json.loads(gt_file.read_text())
    captures: dict[str, Path] = {}
    for cap in sorted(input_dir.glob("*.raw.txt")):
        # filename pattern: <model>.raw.txt — strip suffix
        captures[cap.stem.removesuffix(".raw")] = cap
    return InputFixture(
        input_id=input_dir.name,
        prompt_file=prompt_file,
        ground_truth=ground_truth,
        captures=captures,
    )


def load_inputs(inputs_dir: Path) -> list[InputFixture]:
    """Discover all fixtures under `inputs_dir`."""
    if not inputs_dir.exists():
        return []
    out: list[InputFixture] = []
    for child in sorted(inputs_dir.iterdir()):
        if child.is_dir():
            loaded = _load_input(child)
            if loaded is not None:
                out.append(loaded)
    return out


def _measure_one(raw_text: str, ground_truth_verdict: str | None) -> dict:
    """Parse one captured/live raw_output and return parser metrics."""
    parsed = parse_reflection_report(raw_text)
    verdict = parsed["verdict"] or ""
    is_error = verdict == ERROR_VERDICT
    parseable = verdict in ENUM_VERDICTS
    agreement: bool | None = None
    if ground_truth_verdict is not None:
        agreement = verdict == ground_truth_verdict
    return {
        "verdict": verdict,
        "parseable": parseable,
        "is_error": is_error,
        "has_quality": bool(parsed["quality_assessment"].strip()),
        "has_slop": bool(parsed["slop_detection"].strip()),
        "has_improvements": bool(parsed["improvements"].strip()),
        "has_fix_list": bool(parsed["fix_list"]),
        "has_summary": bool(parsed["verdict_summary"].strip()),
        "verdict_summary": parsed["verdict_summary"],
        "raw_chars": len(raw_text),
        "agreement": agreement,
    }


def _save_capture(model: str, input_id: str, raw_text: str, out_dir: Path) -> Path:
    """Persist a captured live output so it can be replayed offline later."""
    cap_dir = out_dir / "captures" / model
    cap_dir.mkdir(parents=True, exist_ok=True)
    dest = cap_dir / f"{input_id}.raw.txt"
    dest.write_text(raw_text)
    return dest


def run_offline(inputs: list[InputFixture]) -> list[ModelRun]:
    """Replay each (input, model) capture through the parser."""
    runs: list[ModelRun] = []
    for inp in inputs:
        gt = inp.ground_truth_verdict
        for model, cap_path in inp.captures.items():
            raw = cap_path.read_text()
            metrics = _measure_one(raw, gt)
            try:
                rel = str(cap_path.resolve().relative_to(FIXTURES_DIR.parent))
            except ValueError:
                rel = str(cap_path)
            runs.append(ModelRun(
                input_id=inp.input_id,
                model=model,
                raw_output_path=rel,
                **metrics,
            ))
    return runs


def _extract_text_from_stream_json(stream_text: str) -> str:
    """Mirror of `odin.harnesses.base.extract_text_from_stream`.

    Strips stream-json envelope events so only the reviewer's text survives.
    Falls back to the raw text if no envelope is detected.
    """
    try:
        from odin.harnesses.base import extract_text_from_stream
        return extract_text_from_stream(stream_text)
    except Exception:
        return stream_text


def _build_cli_cmd(cli_path: str, cli: str, prompt: str, model: str) -> tuple[list[str], bool]:
    """Return (argv, strip_stream_json) for the requested CLI.

    The two CLIs invoke a one-shot prompt very differently:

    - `claude` uses `-p <prompt> --model <model>` and emits stream-json framing
      (system/init/hook events) that must be stripped before the reviewer text
      reaches the parser — mirrors the production `reflect_task()` path.
    - `opencode` uses `run --model <provider/model> <prompt>` and writes only the
      model's text answer to stdout (its own logs go to stderr), so no envelope
      stripping is needed.

    Keeping the dispatch here (not scattered through run_live) means adding a
    third CLI is a single branch, not a rewrite. Anthropic access is the
    intended default; opencode is the fallback when only opencode auth is
    mounted (sandboxed worktrees with no ANTHROPIC_API_KEY).
    """
    base = os.path.basename(cli).lower()
    if "opencode" in base or "kilo" in base:
        return [cli_path, "run", "--model", model, prompt], False
    # default: claude-style one-shot with stream-json framing
    return (
        [
            cli_path, "-p", prompt,
            "--model", model,
            "--bare",
            "--setting-sources", "user",
            "--output-format", "stream-json",
            "--verbose",
        ],
        True,
    )


def run_live(
    inputs: list[InputFixture],
    models: list[str],
    cli: str,
    out_dir: Path,
) -> list[ModelRun]:
    """Call the reviewer CLI per (input, model) pair and parse the output.

    `claude` is the intended default (`claude -p <prompt> --model <model>`);
    `opencode`/`kilo` are supported for sandboxes where only opencode auth is
    mounted. See `_build_cli_cmd` for the per-CLI invocation.
    """
    runs: list[ModelRun] = []
    cli_path = shutil.which(cli)
    if cli_path is None:
        raise SystemExit(f"CLI {cli!r} not found on PATH; install it or use --mode offline")
    for inp in inputs:
        prompt = inp.prompt_file.read_text()
        gt = inp.ground_truth_verdict
        for model in models:
            cmd, strip_stream = _build_cli_cmd(cli_path, cli, prompt, model)
            t0 = time.monotonic()
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=LIVE_TIMEOUT_SECONDS,
                    env={**os.environ, "CLAUDE_CODE_SIMPLE": "1"},
                )
            except subprocess.TimeoutExpired:
                runs.append(ModelRun(
                    input_id=inp.input_id,
                    model=model,
                    verdict=ERROR_VERDICT,
                    parseable=False,
                    is_error=True,
                    has_quality=False,
                    has_slop=False,
                    has_improvements=False,
                    has_fix_list=False,
                    has_summary=False,
                    verdict_summary=f"<timeout after {LIVE_TIMEOUT_SECONDS}s>",
                    raw_chars=0,
                    agreement=False if gt else None,
                    elapsed_s=time.monotonic() - t0,
                ))
                continue
            elapsed = time.monotonic() - t0
            raw = proc.stdout or ""
            extracted = _extract_text_from_stream_json(raw) if strip_stream else raw
            cap_path = _save_capture(model, inp.input_id, extracted, out_dir)
            metrics = _measure_one(extracted, gt)
            runs.append(ModelRun(
                input_id=inp.input_id,
                model=model,
                raw_output_path=str(cap_path.relative_to(out_dir.parent)),
                elapsed_s=elapsed,
                **metrics,
            ))
    return runs


def aggregate(runs: Iterable[ModelRun]) -> dict[str, dict]:
    """Per-model summary metrics. Used to build the report table."""
    by_model: dict[str, list[ModelRun]] = {}
    for r in runs:
        by_model.setdefault(r.model, []).append(r)
    summary: dict[str, dict] = {}
    for model, items in by_model.items():
        n = len(items)
        parseable = sum(1 for r in items if r.parseable)
        errors = sum(1 for r in items if r.is_error)
        agreements = [r.agreement for r in items if r.agreement is not None]
        agreement_n = len(agreements)
        summary[model] = {
            "n": n,
            "parseable": parseable,
            "errors": errors,
            "agreement_n": agreement_n,
            "agreement_correct": sum(1 for a in agreements if a),
            "parse_pct": 100.0 * parseable / n if n else 0.0,
            "error_pct": 100.0 * errors / n if n else 0.0,
            "agreement_pct": (
                100.0 * sum(1 for a in agreements if a) / agreement_n
                if agreement_n else None
            ),
            "elapsed_s_total": round(sum(r.elapsed_s for r in items), 1),
            "verdict_counts": {
                v: sum(1 for r in items if r.verdict == v)
                for v in (*ENUM_VERDICTS, ERROR_VERDICT)
            },
            "has_fix_list_pct": 100.0 * sum(1 for r in items if r.has_fix_list) / n if n else 0.0,
            "has_summary_pct": 100.0 * sum(1 for r in items if r.has_summary) / n if n else 0.0,
        }
    return summary


def aggregate_by_category(
    inputs: list[InputFixture],
    runs: list[ModelRun],
) -> dict[str, dict[str, dict]]:
    """Per-fixture-category breakdown of the model metrics.

    Real captures (fixture_category == 'real_capture') are the measurement
    evidence the recommendation is built on. Parser-validation synthetic
    fixtures (anything else) are kept separate so they don't inflate the
    real-capture parse% numbers — the operator can see both at a glance.
    """
    cat_by_input: dict[str, str] = {
        inp.input_id: (inp.ground_truth or {}).get("fixture_category", "uncategorised")
        for inp in inputs
    }
    by_cat: dict[str, list[ModelRun]] = {}
    for r in runs:
        cat = cat_by_input.get(r.input_id, "uncategorised")
        by_cat.setdefault(cat, []).append(r)
    return {cat: aggregate(rows) for cat, rows in by_cat.items()}


def render_table(summary: dict[str, dict]) -> str:
    """Format the per-model summary as a markdown table."""
    headers = ("model", "N", "parse%", "agreement%", "ERROR%")
    rows = ["| " + " | ".join(headers) + " |",
            "| " + " | ".join(["---"] * len(headers)) + " |"]
    for model, s in summary.items():
        agreement = (
            f"{s['agreement_pct']:.1f}"
            if s["agreement_pct"] is not None else "—"
        )
        rows.append(
            f"| {model} | {s['n']} | {s['parse_pct']:.1f} | "
            f"{agreement} | {s['error_pct']:.1f} |"
        )
    return "\n".join(rows)


def recommend(summary: dict[str, dict], current_default: str) -> str:
    """Return a one-line recommendation: flip / keep / size-scale.

    Logic (transparent so the operator can challenge it):
    - If a candidate cheap model has parse% >= 95 AND agreement% >= 80 AND
      ERROR% <= 5 with N >= 5: flip is safe.
    - If any cheap candidate clears parse% >= 90 but not the full bar: scale
      (use cheap on small tasks, sonnet on big); the strategy already exists
      (reflection_review_strategy in the codebase).
    - Otherwise: keep current default.

    N<3 is treated as insufficient evidence — return 'insufficient' and let
    the operator decide once the run is scaled up.
    """
    if not summary:
        return "no models measured"
    cheap = [m for m in summary if m != current_default]
    if not cheap:
        return "only baseline model measured — add a cheap candidate to compare"
    smallest_n = min(summary[m]["n"] for m in cheap)
    if smallest_n < 3:
        return (
            f"insufficient evidence (smallest N={smallest_n} on a candidate; "
            f"re-run with N>=5 before flipping)"
        )
    best_cheap = max(
        cheap,
        key=lambda m: (
            summary[m]["parse_pct"],
            summary[m]["agreement_pct"] or 0,
        ),
    )
    s = summary[best_cheap]
    if s["parse_pct"] >= 95 and (s["agreement_pct"] or 0) >= 80 and s["error_pct"] <= 5:
        return (
            f"flip — {best_cheap} clears the bar "
            f"(parse {s['parse_pct']:.0f}%, agreement {s['agreement_pct']:.0f}%, "
            f"ERROR {s['error_pct']:.0f}%)"
        )
    if s["parse_pct"] >= 90 and s["error_pct"] <= 10:
        return (
            f"size-scale — {best_cheap} clears parse% but not full bar "
            f"(parse {s['parse_pct']:.0f}%, agreement {s['agreement_pct'] or 0:.0f}%, "
            f"ERROR {s['error_pct']:.0f}%); wire small tasks to {best_cheap}, keep "
            f"{current_default} on large context"
        )
    return (
        f"keep {current_default} — no candidate clears the bar "
        f"(best {best_cheap}: parse {s['parse_pct']:.0f}%, "
        f"agreement {s['agreement_pct'] or 0:.0f}%, ERROR {s['error_pct']:.0f}%)"
    )


def recommend_from_categories(
    by_cat: dict[str, dict[str, dict]],
    current_default: str,
) -> str:
    """Build the recommendation from the real_capture category only.

    Synthetic parser-validation fixtures don't carry evidence about model
    quality; they're shape-correct by construction. The recommendation
    always reads the real_capture summary.
    """
    real = by_cat.get("real_capture")
    if real:
        return recommend(real, current_default)
    if not by_cat:
        return "no fixtures found"
    return (
        "no real_capture fixtures — recommendation needs at least one captured "
        "review pair; run --mode live with API access to generate evidence"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--mode", choices=("offline", "live"), default="offline")
    ap.add_argument("--inputs-dir", type=Path, default=INPUTS_DIR)
    ap.add_argument(
        "--models", nargs="+", default=["haiku"],
        help="Candidate models to evaluate (live mode). Ignored in offline mode.",
    )
    ap.add_argument(
        "--current-default", default="sonnet",
        help="The board's current pinned reviewer; baseline for the recommendation.",
    )
    ap.add_argument(
        "--cli", default="claude",
        help="CLI binary to invoke in live mode (default: claude).",
    )
    ap.add_argument(
        "--out-dir", type=Path, default=Path(__file__).resolve().parent / "results",
        help="Where to write results.json, summary.md, captures/.",
    )
    ap.add_argument(
        "--recommend", action="store_true",
        help="Print the one-line recommendation after the table.",
    )
    args = ap.parse_args(argv)

    inputs = load_inputs(args.inputs_dir)
    if not inputs:
        print(f"no inputs found under {args.inputs_dir}", file=sys.stderr)
        return 2

    args.out_dir.mkdir(parents=True, exist_ok=True)
    captures_dir = args.out_dir / "captures"
    captures_dir.mkdir(parents=True, exist_ok=True)

    if args.mode == "offline":
        runs = run_offline(inputs)
    else:
        runs = run_live(inputs, args.models, args.cli, args.out_dir)

    summary = aggregate(runs)
    by_cat = aggregate_by_category(inputs, runs)
    table = render_table(summary)

    verdict = (
        recommend_from_categories(by_cat, args.current_default)
        if args.recommend else None
    )

    # Persist artifacts
    try:
        inputs_rel = [str(inp.prompt_file.relative_to(args.out_dir)) for inp in inputs]
    except ValueError:
        # prompts live outside out_dir; fall back to absolute paths
        inputs_rel = [str(inp.prompt_file) for inp in inputs]
    (args.out_dir / "results.json").write_text(json.dumps({
        "mode": args.mode,
        "current_default": args.current_default,
        "inputs": [
            {
                "id": inp.input_id,
                "prompt_file": inputs_rel[i],
                "ground_truth": inp.ground_truth,
            }
            for i, inp in enumerate(inputs)
        ],
        "summary": summary,
        "by_category": by_cat,
        "per_run": [r.to_dict() for r in runs],
        "recommendation": verdict,
    }, indent=2))
    lines = [
        "# Reflection model evaluation",
        "",
        f"Mode: `{args.mode}`",
        f"Current board default: `{args.current_default}`",
        f"Inputs evaluated: {len(inputs)}",
        "",
        "## Per-model summary (all fixtures)",
        "",
        table,
        "",
    ]
    if by_cat:
        lines.append("## By fixture category")
        lines.append("")
        for cat, cat_summary in by_cat.items():
            lines.append(f"### `{cat}`")
            lines.append("")
            lines.append(render_table(cat_summary))
            lines.append("")
    if summary:
        lines.append("## Verdict breakdown (all fixtures)")
        lines.append("")
        lines.append("| model | PASS | NEEDS_WORK | FAIL | ERROR |")
        lines.append("| --- | --- | --- | --- | --- |")
        for model, s in summary.items():
            vc = s["verdict_counts"]
            lines.append(
                f"| {model} | {vc.get('PASS', 0)} | {vc.get('NEEDS_WORK', 0)} | "
                f"{vc.get('FAIL', 0)} | {vc.get('ERROR', 0)} |"
            )
        lines.append("")
        lines.append("## Section coverage (all fixtures)")
        lines.append("")
        lines.append("| model | has_fix_list% | has_summary% |")
        lines.append("| --- | --- | --- |")
        for model, s in summary.items():
            lines.append(
                f"| {model} | {s['has_fix_list_pct']:.0f} | {s['has_summary_pct']:.0f} |"
            )
        lines.append("")
        lines.append("## Recommendation")
        lines.append("")
        lines.append(verdict or "(run with --recommend)")
        lines.append("")
    (args.out_dir / "summary.md").write_text("\n".join(lines))

    # Echo to stdout for the operator
    print(table)
    if verdict:
        print()
        print(f"recommendation: {verdict}")
    print(f"\nartifacts: {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())