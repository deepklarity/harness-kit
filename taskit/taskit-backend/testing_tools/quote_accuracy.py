#!/usr/bin/env python
"""Quote accuracy diagnostic — estimate vs actual across finished tasks.

Reads task.metadata for finished tasks (status in {DONE, FAILED, CANCELED})
and produces a table comparing the dispatch-time estimate with the
completion-time actual. This is the "audit number" for the memory bucket:
how close is the twins-based quote to reality, and where does it drift?

Usage:
    cd taskit/taskit-backend
    python testing_tools/quote_accuracy.py [board_id]
    python testing_tools/quote_accuracy.py [board_id] --brief
    python testing_tools/quote_accuracy.py [board_id] --json
"""
import statistics
import sys

from _utils import (
    setup_django, parse_args, print_json, want_section,
)

setup_django()

from tasks.models import Board, Task, TaskStatus  # noqa: E402


FINISHED_STATUSES = (TaskStatus.TESTING, TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELED)


def _safe_pct(numerator, denominator):
    """Percent error: |actual - estimate| / estimate * 100. None when estimate is 0/None."""
    if not denominator:
        return None
    return abs((numerator or 0) - denominator) / denominator * 100


def _row(task):
    """Extract one (estimate, actual) row from a task's metadata."""
    md = task.metadata or {}
    estimate = md.get("estimate") or {}
    actual = md.get("actual") or {}
    return {
        "task_id": task.id,
        "title": task.title,
        "status": task.status,
        "confidence": estimate.get("confidence"),
        "transition": actual.get("transition"),
        "estimate_tokens": estimate.get("tokens_median"),
        "actual_tokens": actual.get("tokens"),
        "token_error_pct": _safe_pct(actual.get("tokens"), estimate.get("tokens_median")),
        "estimate_duration_ms": estimate.get("duration_ms_median"),
        "actual_duration_ms": actual.get("duration_ms"),
        "duration_error_pct": _safe_pct(actual.get("duration_ms"), estimate.get("duration_ms_median")),
        "source_twin_ids": estimate.get("source_twin_ids", []),
    }


def collect_rows(board=None):
    """Read tasks carrying both estimate and actual metadata, newest first."""
    qs = (
        Task.objects
        .filter(status__in=FINISHED_STATUSES)
        .order_by("-id")
    )
    if board is not None:
        qs = qs.filter(board_id=board.id)
    rows = []
    for task in qs:
        md = task.metadata or {}
        if "estimate" in md and "actual" in md:
            rows.append(_row(task))
    return rows


def aggregate(rows):
    """Median absolute %-error across the rows that have both numbers."""
    token_errs = [r["token_error_pct"] for r in rows if r["token_error_pct"] is not None]
    dur_errs = [r["duration_error_pct"] for r in rows if r["duration_error_pct"] is not None]
    return {
        "row_count": len(rows),
        "with_token_estimate": len(token_errs),
        "with_duration_estimate": len(dur_errs),
        "median_token_error_pct": round(statistics.median(token_errs), 1) if token_errs else None,
        "median_duration_error_pct": round(statistics.median(dur_errs), 1) if dur_errs else None,
    }


def print_report(board=None, mode="standard"):
    rows = collect_rows(board)
    agg = aggregate(rows)
    if mode == "json":
        print_json({
            "board": board.id if board else None,
            "row_count": len(rows),
            "rows": rows,
            "aggregate": agg,
        })
        return
    if mode == "brief":
        scope = f"board #{board.id}" if board else "all boards"
        if agg["row_count"] == 0:
            print(f"quote_accuracy [{scope}]: 0 tasks with estimate+actual")
            return
        tok = agg["median_token_error_pct"]
        dur = agg["median_duration_error_pct"]
        tok_str = f"{tok:.0f}%" if tok is not None else "—"
        dur_str = f"{dur:.0f}%" if dur is not None else "—"
        print(
            f"quote_accuracy [{scope}]: {agg['row_count']} rows · "
            f"median token error {tok_str} · median duration error {dur_str}"
        )
        return

    print(f"\n{'=' * 70}")
    scope = f"BOARD: {board.name} (#{board.id})" if board else "ALL BOARDS"
    print(f"  QUOTE ACCURACY — {scope}")
    print(f"{'=' * 70}")
    print(f"  rows: {agg['row_count']} · token est: {agg['with_token_estimate']} · "
          f"duration est: {agg['with_duration_estimate']}")
    print(f"  median token error:     {agg['median_token_error_pct']}%")
    print(f"  median duration error:  {agg['median_duration_error_pct']}%")
    if not rows:
        print("  (no finished tasks carry estimate + actual yet)")
        return
    print()
    print(f"  {'task':<6} {'status':<10} {'conf':<7} {'est tok':>9} {'act tok':>9} "
          f"{'err%':>6} {'est dur':>10} {'act dur':>10} {'err%':>6}")
    for r in rows:
        et = r["estimate_tokens"]
        at = r["actual_tokens"]
        ed = r["estimate_duration_ms"]
        ad = r["actual_duration_ms"]
        et_s = f"{et:>9,}" if et is not None else f"{'—':>9}"
        at_s = f"{at:>9,}" if at is not None else f"{'—':>9}"
        ed_s = f"{ed / 1000:>8.0f}s" if ed is not None else f"{'—':>10}"
        ad_s = f"{ad / 1000:>8.0f}s" if ad is not None else f"{'—':>10}"
        te = r["token_error_pct"]
        de = r["duration_error_pct"]
        te_s = f"{te:>5.0f}%" if te is not None else f"{'—':>6}"
        de_s = f"{de:>5.0f}%" if de is not None else f"{'—':>6}"
        print(
            f"  #{r['task_id']:<5} {r['status']:<10} {r['confidence'] or '—':<7} "
            f"{et_s} {at_s} {te_s} {ed_s} {ad_s} {de_s}"
        )
    print()


def main():
    positional, mode, _ = parse_args(sys.argv, positional_name="board_id")
    board = None
    if positional:
        try:
            board = Board.objects.get(pk=positional)
        except Board.DoesNotExist:
            print(f"Board #{positional} not found.")
            sys.exit(1)
    print_report(board=board, mode=mode)


if __name__ == "__main__":
    main()