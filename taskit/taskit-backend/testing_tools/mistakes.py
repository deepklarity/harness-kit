#!/usr/bin/env python
"""Mistakes ledger diagnostic (task #223).

Lists the distilled one-liners recorded for rejected/failed tasks so the
patterns behind rework and failures are scannable at a glance. Each entry
is one line: what failed, how it was classified, and where it came from.

Usage:
    cd taskit/taskit-backend
    python testing_tools/mistakes.py [board_id]
    python testing_tools/mistakes.py [board_id] --brief
    python testing_tools/mistakes.py [board_id] --json
    python testing_tools/mistakes.py --spec <spec_id> --json
"""
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _utils import setup_django, parse_args, print_json  # noqa: E402

setup_django()

from tasks.models import Board, MistakeEntry  # noqa: E402
from tasks.mistakes import serialize_mistake  # noqa: E402


def _query(board_id=None, spec_id=None):
    qs = MistakeEntry.objects.select_related("task", "spec")
    if spec_id:
        qs = qs.filter(spec_id=spec_id)
    elif board_id:
        qs = qs.filter(task__board_id=board_id)
    return qs.order_by("-created_at", "-id")


def build_payload(board_id=None, spec_id=None):
    """Structured ledger payload (board_id / spec_id scope the query)."""
    entries = list(_query(board_id=board_id, spec_id=spec_id))
    classes = Counter(e.failure_class for e in entries if e.failure_class)
    sources = Counter(e.source for e in entries)
    return {
        "board_id": int(board_id) if board_id else None,
        "spec_id": int(spec_id) if spec_id else None,
        "count": len(entries),
        "by_source": dict(sources),
        "by_failure_class": dict(classes),
        "mistakes": [serialize_mistake(e) for e in entries],
    }


def list_mistakes(mode="standard", board_id=None, spec_id=None):
    """Print (brief/standard) or return (json) the ledger payload."""
    data = build_payload(board_id=board_id, spec_id=spec_id)
    if mode == "brief":
        _print_brief(data)
    elif mode in ("standard", "full"):
        _print_standard(data)
    # json: the caller (main / tests) consumes the returned dict.
    return data


def _print_brief(data):
    scope = _scope_label(data)
    cls = ", ".join(f"{c} {k or 'unclassified'}" for k, c in sorted(data["by_failure_class"].items())) or "none"
    src = ", ".join(f"{c} {k}" for k, c in sorted(data["by_source"].items())) or "none"
    print(f"Mistakes ledger {scope}: {data['count']} entries ({src}) | classes: {cls}")


def _print_standard(data):
    scope = _scope_label(data)
    print(f"\n{'=' * 70}")
    print(f"  MISTAKES LEDGER{scope}")
    print(f"{'=' * 70}")
    print(f"  entries: {data['count']}")
    if data["by_source"]:
        src = ", ".join(f"{c} {k}" for k, c in sorted(data["by_source"].items()))
        print(f"  by source: {src}")
    if data["by_failure_class"]:
        cls = ", ".join(f"{c} {k}" for k, c in sorted(data["by_failure_class"].items()))
        print(f"  by class:  {cls}")
    print(f"  {'-' * 66}")
    if not data["mistakes"]:
        print("  (no mistakes recorded)")
        print()
        return
    for e in data["mistakes"]:
        cls = f" {{{e['failure_class']}}}" if e["failure_class"] else ""
        verdict = f" [{e['verdict']}]" if e["verdict"] else ""
        agent = f" {e['agent']}" if e["agent"] else ""
        print(
            f"  #{e['task_id']:<4} ({e['source']}){verdict}{cls} {e['one_liner']}{agent}"
        )
    print()


def _scope_label(data):
    if data["spec_id"]:
        return f" (spec #{data['spec_id']})"
    if data["board_id"]:
        return f" (board #{data['board_id']})"
    return " (all)"


def _parse_spec_flag(argv):
    for i, arg in enumerate(argv[1:], start=1):
        if arg == "--spec" and i + 1 < len(argv):
            try:
                return int(argv[i + 1])
            except (TypeError, ValueError):
                return argv[i + 1]
    return None


def main():
    positional, mode, _ = parse_args(sys.argv, positional_name="board_id")
    spec_id = _parse_spec_flag(sys.argv)
    board_id = positional if (positional and not spec_id) else None

    # When a name is given instead of an id, resolve it.
    if board_id and not str(board_id).isdigit():
        try:
            board_id = Board.objects.get(name=board_id).id
        except Board.DoesNotExist:
            print(f"Board '{board_id}' not found.")
            sys.exit(1)

    data = list_mistakes(mode=mode, board_id=board_id, spec_id=spec_id)
    if mode == "json":
        print_json(data)


if __name__ == "__main__":
    main()
