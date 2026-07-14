#!/usr/bin/env python3
"""Speed + cost probe: default model per harness, one poem prompt (M2)."""

from __future__ import annotations

import argparse
import csv
import json
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve()
_HARNESS_KIT = _HERE.parents[2]
_ODIN_SRC = _HARNESS_KIT / "odin" / "src"
if _ODIN_SRC.exists() and str(_ODIN_SRC) not in sys.path:
    sys.path.insert(0, str(_ODIN_SRC))

from odin.harnesses import claude, codex, gemini, glm, minimax  # noqa: E402
from odin.models import AgentConfig  # noqa: E402

AGENT_MODELS = _HARNESS_KIT / "taskit" / "taskit-backend" / "data" / "agent_models.json"
CSV_PATH = _HERE.parent / "speed_log.csv"
PROMPT = "Write a 12-line poem about the sea. Output only the poem."
TIMEOUT_S = 120

HARNESSES = {
    "claude": claude.ClaudeHarness,
    "codex": codex.CodexHarness,
    "gemini": gemini.GeminiHarness,
    "glm": glm.GLMHarness,
    "minimax": minimax.MiniMaxHarness,
}

CSV_FIELDS = [
    "run_id", "timestamp", "harness", "model", "wall_ms", "exit_code",
    "stdout_chars", "stdout_lines",
    "input_tokens", "output_tokens", "cost_usd",
    "error",
]


@dataclass
class Row:
    run_id: str
    timestamp: str
    harness: str
    model: str
    wall_ms: int
    exit_code: int
    stdout_chars: int
    stdout_lines: int
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    error: str = ""


def load_harness_defaults() -> dict[str, dict]:
    data = json.loads(AGENT_MODELS.read_text())
    return {
        name: {"cli": info["cli_command"], "model": info["default_model"]}
        for name, info in data["agents"].items()
        if name in HARNESSES
    }


def load_pricing(agent: str, model: str) -> tuple[Optional[float], Optional[float]]:
    """Return (input_price_per_1m, output_price_per_1m) or (None, None)."""
    data = json.loads(AGENT_MODELS.read_text())
    info = data["agents"].get(agent) or {}
    for m in info.get("models", []):
        if m["name"] == model:
            return (
                m.get("input_price_per_1m_tokens"),
                m.get("output_price_per_1m_tokens"),
            )
    return (None, None)


def estimate_cost(
    input_tokens: Optional[int],
    output_tokens: Optional[int],
    price_in: Optional[float],
    price_out: Optional[float],
) -> Optional[float]:
    if None in (input_tokens, output_tokens, price_in, price_out):
        return None
    return (input_tokens / 1_000_000) * price_in + (output_tokens / 1_000_000) * price_out


def _extract_from_event(obj: dict) -> str:
    """Pull the response text from a single stream-JSON event for any of the 6 harnesses."""
    t = obj.get("type")

    if t == "content_block_delta":
        return (obj.get("delta") or {}).get("text", "") or ""

    if t == "result":
        r = obj.get("result")
        if isinstance(r, str):
            return r

    if t == "message" and obj.get("role") == "assistant":
        c = obj.get("content")
        if isinstance(c, str):
            return c

    if t == "assistant":
        msg = obj.get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):
            return "".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )

    if t == "item.completed":
        item = obj.get("item") or {}
        if item.get("type") == "agent_message":
            return item.get("text", "")

    if t == "text":
        part = obj.get("part")
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            return part["text"]
        return obj.get("content") or obj.get("text") or ""

    return ""


def extract_text(raw: str) -> str:
    """Extract the response text from raw CLI stdout. Handles stream-JSON and plain text."""
    if not raw.strip():
        return ""
    first = next((l for l in raw.splitlines() if l.strip()), "")
    if not first.startswith("{"):
        return raw
    parts: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            chunk = _extract_from_event(obj)
            if chunk:
                parts.append(chunk)
    return "".join(parts)


def parse_tokens(raw_stdout: str) -> dict[str, int]:
    """Best-effort token parser across known stream-JSON shapes. Returns {} if unparseable.

    Shapes handled (verified against live CLI output):
      - Claude:         {"modelUsage": {"<model>": {"inputTokens", "outputTokens"}}}
      - Codex:          {"type": "turn.completed", "usage": {"input_tokens", "output_tokens"}}
      - Gemini:         {"type": "result", "stats": {"input_tokens", "output_tokens"}}
      - opencode/kilo:  {"type": "step_finish", "part": {"tokens": {"input", "output"}}}
    """
    in_tok = 0
    out_tok = 0
    found = False

    def _extract(d: dict) -> tuple[int, int]:
        if not isinstance(d, dict):
            return 0, 0
        ui = d.get("input_tokens") or d.get("inputTokens") or d.get("input") or 0
        uo = d.get("output_tokens") or d.get("outputTokens") or d.get("output") or 0
        return ui, uo

    def _has_token_keys(d) -> bool:
        return isinstance(d, dict) and any(
            k in d for k in ("input_tokens", "output_tokens", "inputTokens", "outputTokens", "input", "output")
        )

    for line in raw_stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue

        mu = obj.get("modelUsage")
        if isinstance(mu, dict):
            for md in mu.values():
                if isinstance(md, dict):
                    in_tok += md.get("inputTokens", 0)
                    out_tok += md.get("outputTokens", 0)
            found = True
            continue

        if obj.get("type") == "turn.completed" and _has_token_keys(obj.get("usage")):
            ui, uo = _extract(obj["usage"])
            in_tok = max(in_tok, ui)
            out_tok = max(out_tok, uo)
            found = True
            continue

        if obj.get("type") == "result":
            for key in ("usage", "stats"):
                if _has_token_keys(obj.get(key)):
                    ui, uo = _extract(obj[key])
                    in_tok = max(in_tok, ui)
                    out_tok = max(out_tok, uo)
                    found = True
                    break

        if obj.get("type") == "step_finish":
            tokens = (obj.get("part") or {}).get("tokens") or (obj.get("result") or {}).get("tokens") or {}
            if _has_token_keys(tokens):
                ui, uo = _extract(tokens)
                in_tok += ui
                out_tok += uo
                found = True

    return {"input_tokens": in_tok, "output_tokens": out_tok} if found else {}


def build_command(harness: str, cli: str, model: str) -> list[str]:
    return HARNESSES[harness](AgentConfig(cli_command=cli)).build_execute_command(
        PROMPT, {"model": model}
    )


def run_one(harness: str, cli: str, model: str, run_id: str, dry_run: bool) -> Row:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cmd = build_command(harness, cli, model)

    if dry_run:
        print(f"[dry-run] {harness}: {' '.join(shlex.quote(c) for c in cmd)}")
        return Row(run_id, ts, harness, model, 0, 0, 0, 0, error="dry-run")

    if shutil.which(cli) is None:
        return Row(run_id, ts, harness, model, 0, -1, 0, 0,
                   error=f"{Path(cli).name} not installed")

    start = time.perf_counter()
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return Row(run_id, ts, harness, model, TIMEOUT_S * 1000, -2, 0, 0,
                   error=f"timeout {TIMEOUT_S}s")

    wall_ms = int((time.perf_counter() - start) * 1000)
    raw = proc.stdout.decode("utf-8", errors="replace")
    text = extract_text(raw)
    err = ""
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        err = (stderr or "non-zero exit")[:200]

    tokens = parse_tokens(raw)
    in_tok = tokens.get("input_tokens")
    out_tok = tokens.get("output_tokens")
    price_in, price_out = load_pricing(harness, model)
    cost = estimate_cost(in_tok, out_tok, price_in, price_out)

    return Row(
        run_id=run_id, timestamp=ts, harness=harness, model=model,
        wall_ms=wall_ms, exit_code=proc.returncode,
        stdout_chars=len(text), stdout_lines=len(text.splitlines()),
        input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost,
        error=err,
    )


def _needs_migration(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    with path.open() as f:
        header = next(csv.reader(f), [])
    return header != CSV_FIELDS


def _migrate_csv(path: Path) -> None:
    """Rewrite CSV with current CSV_FIELDS, padding old rows with empty values for new columns."""
    with path.open(newline="") as f:
        old_rows = list(csv.DictReader(f))
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in old_rows:
            w.writerow({k: r.get(k, "") for k in CSV_FIELDS})


def append_csv(rows: list[Row], path: Path = CSV_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if _needs_migration(path):
        _migrate_csv(path)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if write_header:
            w.writeheader()
        for r in rows:
            row = asdict(r)
            for k in ("input_tokens", "output_tokens", "cost_usd"):
                if row[k] is None:
                    row[k] = ""
            w.writerow(row)


def render_markdown(rows: list[Row], total_ms: int) -> str:
    """Render an aligned markdown table. Pipes align vertically in terminal;
    still valid markdown (Slack/GitHub ignore the extra padding on render)."""
    headers = ["Harness", "Model", "Wall (s)", "Chars", "Lines", "In tok", "Out tok", "Cost (USD)", "Status"]
    right_align = {2, 3, 4, 5, 6, 7}

    text_rows: list[list[str]] = []
    for r in sorted(rows, key=lambda x: x.wall_ms):
        status = "ok" if r.exit_code == 0 else (r.error[:40] or f"exit {r.exit_code}")
        in_s = str(r.input_tokens) if r.input_tokens is not None else "N/A"
        out_s = str(r.output_tokens) if r.output_tokens is not None else "N/A"
        cost_s = f"${r.cost_usd:.5f}" if r.cost_usd is not None else "N/A"
        text_rows.append([
            r.harness, f"`{r.model}`", f"{r.wall_ms/1000:.2f}",
            str(r.stdout_chars), str(r.stdout_lines),
            in_s, out_s, cost_s, status,
        ])

    widths = [max(len(h), *(len(row[i]) for row in text_rows)) for i, h in enumerate(headers)]

    def fmt_row(cells: list[str]) -> str:
        return "| " + " | ".join(
            (cell.rjust(widths[i]) if i in right_align else cell.ljust(widths[i]))
            for i, cell in enumerate(cells)
        ) + " |"

    sep = "|" + "|".join(
        (("-" * (widths[i] + 1) + ":") if i in right_align else ("-" * (widths[i] + 2)))
        for i in range(len(headers))
    ) + "|"

    lines = [fmt_row(headers), sep]
    lines.extend(fmt_row(row) for row in text_rows)
    total_cost = sum((r.cost_usd or 0) for r in rows)
    lines.append("")
    lines.append(f"**Total wall time:** {total_ms/1000:.2f}s  |  **Total cost:** ${total_cost:.5f}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print commands without running")
    args = parser.parse_args()

    defaults = load_harness_defaults()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    rows: list[Row] = []
    start = time.perf_counter()
    for harness, info in defaults.items():
        r = run_one(harness, info["cli"], info["model"], run_id, args.dry_run)
        rows.append(r)
        if not args.dry_run:
            tag = "ok" if r.exit_code == 0 else (r.error or f"exit={r.exit_code}")
            cost_s = f"${r.cost_usd:.5f}" if r.cost_usd is not None else "N/A"
            print(f"  {harness:<8} {r.wall_ms/1000:>7.2f}s  {cost_s:<9}  {tag}")
    total_ms = int((time.perf_counter() - start) * 1000)

    if args.dry_run:
        return 0

    append_csv(rows)
    print()
    print(render_markdown(rows, total_ms))
    print(f"\nAppended {len(rows)} rows to {CSV_PATH.relative_to(_HARNESS_KIT)} (run_id={run_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
