"""CLI: per-(agent, model) league table for one board.

Mirrors testing_tools/routing_suggest.py bootstrap and uses the shared
``_utils.parse_args`` for output mode selection. Backed by
``tasks.league.compute_league_for_board`` — same compute as the
``/api/boards/{id}/league/`` endpoint.

Run from taskit/taskit-backend with the same Django settings the
other diagnostic scripts use:

    python testing_tools/league_table.py <board_id>
    python testing_tools/league_table.py <board_id> --brief
    python testing_tools/league_table.py <board_id> --json
    python testing_tools/league_table.py <board_id> --since-spec <odin_id>
"""

import json
import logging
import os
import sys
from pathlib import Path

# Bootstrap Django so tasks.* and odin.* both resolve. odin lives at
# ../odin/src — prepend its parent (the odin repo's src dir) to
# sys.path. Mirrors routing_suggest.py.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_BACKEND_PARENT = _BACKEND_DIR.parent
_ODIN_SRC = _BACKEND_PARENT.parent / "odin" / "src"

for path in (str(_BACKEND_DIR), str(_ODIN_SRC)):
    if path not in sys.path:
        sys.path.insert(0, path)

import django  # noqa: E402

django.setup()

logging.getLogger().setLevel(logging.WARNING)
logging.getLogger("tasks").setLevel(logging.WARNING)

from _utils import parse_args, want_section  # noqa: E402
from tasks.league import compute_league_for_board  # noqa: E402
from tasks.models import Board  # noqa: E402


ALL_SECTIONS = {"header", "rows"}


# ── Formatting ──────────────────────────────────────────────────────


def _fmt_tokens(n: float) -> str:
    if not n:
        return "0"
    n = float(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return f"{int(n)}"


def _fmt_duration(ms: float) -> str:
    if not ms:
        return "0s"
    seconds = float(ms) / 1000.0
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = seconds / 60.0
    if minutes < 60:
        return f"{minutes:.1f}m"
    hours = minutes / 60.0
    return f"{hours:.1f}h"


def _trunc(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def _brief_line(r: dict) -> str:
    agent = _trunc(str(r["agent"]), 12)
    model = _trunc(str(r["model"]), 24)
    pct = int(round(r["hands_free_pct"] * 100))
    return (
        f"  {agent}/{model} "
        f"t={r['tasks_landed']} "
        f"hf={pct}% "
        f"redo={r['redo_rounds_avg']:.1f} "
        f"tok={_fmt_tokens(r['tokens_median'])} "
        f"dur={_fmt_duration(r['duration_ms_median'])} "
        f"conf={r['merge_conflicts_caused']} "
        f"${r['cost_usd_total']:.2f}"
    )


def _print_table(board: Board, rows: list[dict], meta: dict) -> None:
    title = f"Board {board.id} '{board.name}'"
    print()
    print(f"{'=' * 96}")
    print(f"  LEAGUE TABLE — {title}")
    if meta.get("since_spec"):
        print(f"  since_spec: {meta['since_spec']}")
    print(f"  tasks_landed: {meta['task_count']}  rows: {len(rows)}")
    print(f"{'=' * 96}")
    header = (
        f"  {'agent':<12} {'model':<28} {'tasks':>5} "
        f"{'hands-free':>10} {'redo':>6} {'tokens':>10} "
        f"{'duration':>10} {'conflicts':>9} {'cost':>10}"
    )
    print(header)
    print(f"  {'-' * 12} {'-' * 28} {'-' * 5} "
          f"{'-' * 10} {'-' * 6} {'-' * 10} "
          f"{'-' * 10} {'-' * 9} {'-' * 10}")
    for r in rows:
        agent = _trunc(str(r["agent"]), 12)
        model = _trunc(str(r["model"]), 28)
        pct = r["hands_free_pct"] * 100
        print(
            f"  {agent:<12} {model:<28} {r['tasks_landed']:>5} "
            f"{pct:>9.1f}% {r['redo_rounds_avg']:>6.2f} "
            f"{_fmt_tokens(r['tokens_median']):>10} "
            f"{_fmt_duration(r['duration_ms_median']):>10} "
            f"{r['merge_conflicts_caused']:>9} "
            f"${r['cost_usd_total']:>9.2f}"
        )


# ── Entry point ──────────────────────────────────────────────────────


def _extract_since_spec(argv: list[str]) -> str | None:
    if "--since-spec" in argv:
        i = argv.index("--since-spec")
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def main() -> None:
    positional, mode, sections = parse_args(
        sys.argv, positional_name="board_id"
    )
    since_spec = _extract_since_spec(sys.argv)

    if positional is None:
        sys.exit(
            "Usage: league_table.py <board_id> "
            "[--brief|--json|--since-spec <odin_id>]"
        )

    try:
        board_id = int(positional)
    except ValueError:
        sys.exit(f"Board id must be an integer; got {positional!r}.")

    try:
        board = Board.objects.get(pk=board_id)
    except Board.DoesNotExist:
        sys.exit(f"Board #{board_id} not found.")

    rows = compute_league_for_board(board, since_spec=since_spec)
    row_dicts = [r.to_dict() for r in rows]
    meta = {
        "board_id": board.id,
        "task_count": sum(r["tasks_landed"] for r in row_dicts),
        "since_spec": since_spec,
    }

    if mode == "json":
        print(json.dumps({"rows": row_dicts, "meta": meta}, indent=2))
        return

    if want_section("header", sections):
        title = f"Board {board.id} '{board.name}'"
        since = f" (since_spec={since_spec})" if since_spec else ""
        print(f"{title} — league table "
              f"({meta['task_count']} tasks){since}")

    if want_section("rows", sections):
        if mode == "brief":
            if not row_dicts:
                print("  (no landed tasks)")
            for r in row_dicts:
                print(_brief_line(r))
        else:
            _print_table(board, row_dicts, meta)


if __name__ == "__main__":
    main()