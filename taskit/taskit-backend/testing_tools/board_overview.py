#!/usr/bin/env python
"""Board-level overview diagnostic.

Quick summary of all specs and their task statuses on a board.

Usage:
    cd taskit/taskit-backend
    python testing_tools/board_overview.py [board_id]
    python testing_tools/board_overview.py [board_id] --brief
    python testing_tools/board_overview.py [board_id] --json
"""
import sys
from collections import Counter

from _utils import (
    setup_django, parse_args,
    extract_token_parts, format_duration,
    print_json,
)

setup_django()

from tasks.models import Board, Spec, Task  # noqa: E402


def overview_board(board, mode="standard"):
    """Print or return overview for a single board.

    Args:
        board: Board model instance.
        mode: 'brief' | 'standard' | 'full' | 'json'.
    """
    specs = list(Spec.objects.filter(board=board).order_by("-created_at"))
    all_tasks = list(
        Task.objects.filter(board=board)
        .select_related("assignee")
        .order_by("id")
    )
    tasks_by_spec = {}
    orphan_tasks = []
    for t in all_tasks:
        if t.spec_id:
            tasks_by_spec.setdefault(t.spec_id, []).append(t)
        else:
            orphan_tasks.append(t)

    # ── JSON mode ──
    if mode == "json":
        data = {
            "board": {"id": board.id, "name": board.name},
            "spec_count": len(specs),
            "task_count": len(all_tasks),
            "status_distribution": dict(Counter(t.status for t in all_tasks)),
            "specs": [],
        }
        for spec in specs:
            spec_tasks = tasks_by_spec.get(spec.id, [])
            agg_tokens = sum(extract_token_parts(t.metadata)[0] for t in spec_tasks)
            data["specs"].append({
                "id": spec.id,
                "title": spec.title,
                "abandoned": spec.abandoned,
                "task_count": len(spec_tasks),
                "status_distribution": dict(Counter(t.status for t in spec_tasks)),
                "total_tokens": agg_tokens,
            })
        if orphan_tasks:
            data["orphan_task_count"] = len(orphan_tasks)
        print_json(data)
        return

    # ── Brief mode ──
    if mode == "brief":
        status_counts = Counter(t.status for t in all_tasks)
        status_str = ", ".join(f"{c} {s}" for s, c in sorted(status_counts.items()))
        agg_tokens = sum(extract_token_parts(t.metadata)[0] for t in all_tasks)
        tok = f"{agg_tokens:,}" if agg_tokens else "-"
        print(f"Board #{board.id} '{board.name}': {len(specs)} specs, {len(all_tasks)} tasks ({status_str}) | {tok} tokens")
        for spec in specs:
            spec_tasks = tasks_by_spec.get(spec.id, [])
            sc = Counter(t.status for t in spec_tasks)
            s_str = ", ".join(f"{c} {s}" for s, c in sorted(sc.items()))
            ab = " [ABANDONED]" if spec.abandoned else ""
            print(f"  Spec #{spec.id}: {spec.title[:40]}{ab} ({len(spec_tasks)} tasks: {s_str})")
        return

    # ── Standard / Full mode ──
    print(f"\n{'=' * 70}")
    print(f"  BOARD: {board.name} (#{board.id})")
    print(f"{'=' * 70}")

    if not specs and not orphan_tasks:
        print("  (empty board)")
        return

    for spec in specs:
        spec_tasks = tasks_by_spec.get(spec.id, [])
        status_counts = Counter(t.status for t in spec_tasks)
        status_summary = " | ".join(
            f"{status}: {count}" for status, count in sorted(status_counts.items())
        )
        abandoned = " [ABANDONED]" if spec.abandoned else ""
        print(f"\n  Spec #{spec.id}: {spec.title}{abandoned}")
        print(f"    Tasks: {len(spec_tasks)} ({status_summary})")

        for t in spec_tasks:
            assignee = t.assignee.name if t.assignee else "-"
            model = t.model_name or "-"
            deps = f" deps=[{', '.join(t.depends_on)}]" if t.depends_on else ""
            print(f"      #{t.id:<4} {t.status:<14} {t.title[:35]:<37} {assignee:<12} {model}{deps}")

    if orphan_tasks:
        print(f"\n  Unassociated Tasks ({len(orphan_tasks)}):")
        status_counts = Counter(t.status for t in orphan_tasks)
        status_summary = " | ".join(
            f"{status}: {count}" for status, count in sorted(status_counts.items())
        )
        print(f"    ({status_summary})")
        for t in orphan_tasks:
            assignee = t.assignee.name if t.assignee else "-"
            print(f"      #{t.id:<4} {t.status:<14} {t.title[:35]:<37} {assignee}")

    print()


def main():
    positional, mode, _ = parse_args(sys.argv, positional_name="board_id")

    if positional:
        try:
            board = Board.objects.get(pk=positional)
        except Board.DoesNotExist:
            print(f"Board #{positional} not found.")
            sys.exit(1)
        overview_board(board, mode=mode)
    else:
        boards = Board.objects.all().order_by("id")
        if not boards.exists():
            print("No boards found.")
            sys.exit(0)
        for board in boards:
            overview_board(board, mode=mode)


if __name__ == "__main__":
    main()
