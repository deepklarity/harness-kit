#!/usr/bin/env python3
"""Quickstart sample loader — pick the best available provider and run the sample.

The kit's default ``base_agent`` is ``claude``. A user whose only working
provider is, say, ``glm`` or ``gemini`` therefore cannot plan out of the box —
``odin plan`` would try to invoke the unavailable ``claude`` CLI and fail.

This loader closes that gap. It:

1. Asks ``odin doctor`` which providers are installed + authenticated (reusing
   doctor's probe logic — no provider list of our own).
2. Picks the best available one, ranked by the kit's own ``DEFAULT_MODEL_ROUTING``
   (cheapest-capable first). Any single provider works; nothing is hardcoded to
   claude or glm.
3. Runs the sample spec with ``--base-agent <picked>`` so both decomposition and
   execution use a provider that actually exists on this machine.

Usage:
    python3 odin/sample_specs/quickstart/run.py            # pick + plan + queue
    python3 odin/sample_specs/quickstart/run.py --dry-run   # show the pick only

Run from the repo root, with ``./dev.sh`` up in another terminal (TaskIt backend)
for full dispatch → sandbox → review → merge.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

SPEC_FILE = Path(__file__).resolve().parent / "quickstart_spec.md"


# ── pure selection logic (unit-tested in test_quickstart_loader.py) ┄┄┄┄┄┄┄


def available_providers(doctor_json: dict) -> list[str]:
    """Return provider names doctor reports as available, in doctor's order.

    A provider is "available" when its CLI is installed AND authenticated
    (``AgentProbe.available``). Doctor emits one entry per probed provider.
    """
    return [a["name"] for a in doctor_json.get("agents", []) if a.get("available")]


def derive_preference() -> list[str]:
    """Cheapest-capable-first provider order, taken from the kit's own routing.

    Sources ``DEFAULT_MODEL_ROUTING`` from ``odin.config`` (the same priority
    list ``odin`` itself walks when routing tasks) and de-duplicates it. We do
    not maintain our own ranking — if the kit's preferences change, this tracks
    automatically.

    If ``odin.config`` cannot be imported (e.g. odin was installed via ``pipx``
    in an isolated env, so the ``odin`` command is on PATH but not importable by
    this process), we return ``[]``. ``pick_best`` then falls back to doctor's
    first available provider — correct for the single-provider case (the only
    provider present is picked regardless), merely unranked for multi-provider.
    """
    try:
        from odin.config import DEFAULT_MODEL_ROUTING  # local: odin must be importable
    except ImportError:
        return []

    seen: set[str] = set()
    preference: list[str] = []
    for agent, _model in DEFAULT_MODEL_ROUTING:
        if agent not in seen:
            seen.add(agent)
            preference.append(agent)
    return preference


def pick_best(available: list[str], preference: list[str]) -> Optional[str]:
    """Choose the provider to run the sample with.

    - No providers  → None (caller reports the blocker).
    - Walk ``preference`` (cheapest first); first available match wins.
    - If none of ``available`` appears in ``preference`` (e.g. only ``agy`` or a
      brand-new provider doctor learned about), fall back to the first available
      in doctor's reported order. This is the "no hardcoded assumptions" guard:
      any single provider present is enough.
    """
    if not available:
        return None
    for agent in preference:
        if agent in available:
            return agent
    return available[0]


def plan_command(best: str, spec_file: str | Path) -> list[str]:
    """The ``odin plan`` invocation that runs the sample on the chosen provider.

    ``--base-agent`` is the whole point: it overrides the kit's default
    ``claude`` decomposition agent so planning uses a provider that is actually
    present. ``--auto`` non-interactively decomposes and (with the TaskIt
    backend ``./dev.sh`` brings up) auto-queues tasks for the DAG executor.
    """
    return ["odin", "plan", str(spec_file), "--auto", "--base-agent", best]


# ── I/O: doctor probe + odin plan ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄


def _extract_json(text: str) -> dict:
    """Parse the first JSON object in ``text``, tolerating leading/trailing noise.

    Console logs route to stderr (not stdout), so ``odin doctor --json`` stdout
    is already clean JSON and a plain ``json.loads`` works. We still decode from
    the first ``{`` as defense-in-depth: if a future caller pipes merged
    stdout+stderr, or another writer emits a preamble, parsing stays robust.
    """
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object found in doctor output")
    obj, _end = json.JSONDecoder().raw_decode(text[start:])
    return obj


def _run_doctor() -> dict:
    """Invoke ``odin doctor --json`` and return the parsed report.

    Doctor exits non-zero (1) when no agent is available but still emits valid
    JSON, so we parse stdout regardless of exit code. Raises SystemExit with a
    human-readable message if ``odin`` is missing or output is unparseable.
    """
    try:
        proc = subprocess.run(
            ["odin", "doctor", "--json"], capture_output=True, text=True, timeout=120
        )
    except FileNotFoundError:
        sys.exit(
            "`odin` is not on PATH. Install it first:\n"
            "    pip install -e odin/\n"
            "Then re-run this loader."
        )
    except subprocess.TimeoutExpired:
        sys.exit("`odin doctor --json` timed out. Run `odin doctor` manually to debug.")
    try:
        return _extract_json(proc.stdout)
    except (ValueError, json.JSONDecodeError):
        sys.exit(
            "Could not parse `odin doctor --json` output.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}\n"
            "Run `odin doctor` directly to debug."
        )


def _guidance_for_no_provider() -> str:
    """Concrete next steps when doctor finds no usable provider."""
    return (
        "No provider is installed + authenticated, so odin cannot plan or run.\n"
        "Install / authenticate ONE of:\n"
        "  • claude  — Claude Code CLI (`claude`)         run `claude setup-token`\n"
        "  • codex   — Codex CLI (`codex`)                run `codex login` (writes ~/.codex/auth.json)\n"
        "  • glm     — OpenCode CLI + ZAI_API_KEY         export ZAI_API_KEY=...\n"
        "  • minimax — OpenCode CLI + MINIMAX_API_KEY     export MINIMAX_API_KEY=...\n"
        "  • agy     — agy CLI + AGY_SECRET               export AGY_SECRET=...\n"
        "Then re-run `odin doctor` and this loader."
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the quickstart sample on the best available provider.")
    parser.add_argument("--dry-run", action="store_true", help="pick + print the provider, do not run odin plan")
    args = parser.parse_args(argv)

    if not SPEC_FILE.exists():
        sys.exit(f"Sample spec not found next to this loader: {SPEC_FILE}")

    report = _run_doctor()
    available = available_providers(report)
    if not available:
        print(_guidance_for_no_provider())
        return 1

    best = pick_best(available, derive_preference())
    print(f"Available providers: {', '.join(available)}")
    print(f"Selected for sample: {best}")

    if args.dry_run:
        print("\n--dry-run set; not running odin plan. The command would be:")
        print("  " + " ".join(plan_command(best, SPEC_FILE)))
        return 0

    print(f"\nPlanning the sample spec with --base-agent {best} ...\n")
    result = subprocess.run(plan_command(best, SPEC_FILE))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
