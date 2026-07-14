#!/usr/bin/env python
"""Autonomy metrics diagnostic — BACKLOG item 7.

Ratchet rule: metrics from scripts, not hand counts. Computes
agent-authored merge rate, operator touches, cost per merged change,
and time-to-verified percentiles from the TaskIt database directly.

Usage:
    cd taskit/taskit-backend
    python testing_tools/autonomy_metrics.py [board_id] [--brief|--full|--json]
    python testing_tools/autonomy_metrics.py --brief            # all boards
    python testing_tools/autonomy_metrics.py 5 --json          # board #5 JSON
    python testing_tools/autonomy_metrics.py --spec 49         # one spec

What this counts:
  * Agent-authored merge rate — DONE tasks whose entire lifecycle shows
    no operator intervention. Operator intervention = a manual status
    transition by a non-agent, system email, or operator steering
    comment (question / reply).
  * Operator touches — manual transitions (TaskHistory.status rows where
    changed_by is a human) plus steering comments (operator-authored
    TaskComment with comment_type in {"question","reply"}).
  * Cost per merged change — average and median total tokens across
    DONE tasks, plus a count of tasks with zero captured tokens (the
    honest gap report). Tokens come from the trace comment via
    compute_usage_from_trace, the same source spec_trace uses.
  * Time-to-verified percentiles — dispatch (first EXECUTING/IN_PROGRESS
    transition) to DONE, plus an inner exec-duration view.

Honest gaps: when no usage is captured we say so — we never substitute
a fake number for missing data. Wave 2's audit row
("Token/cost per merged change: none captured (regression)") is
exactly the case where this script would now report 0 tokens and
18/18 capture gaps.
"""
import sys
import re
from collections import defaultdict
from datetime import datetime, timezone

from _utils import (
    setup_django, parse_args, want_section,
    print_json,
)

setup_django()

from tasks.agent_stats import (  # noqa: E402
    DISPATCH_STATUSES,
    OPERATOR_TERMINAL_STATUSES,
    STEERING_COMMENT_TYPES,
    is_human_author,
    is_operator_email,
    percentile,
)
from tasks.execution_processing import compute_usage_from_trace  # noqa: E402
from tasks.models import (  # noqa: E402
    Board,
    Spec,
    Task,
    TaskComment,
    TaskHistory,
    TaskStatus,
)


# Re-exports so existing callers (and the test suite under
# tests/test_autonomy_metrics.py) keep working after the extraction.
__all__ = [
    "is_human_author",
    "is_operator_email",
    "percentile",
    "classify_task",
    "compute_board_metrics",
    "compute_ladder_metrics",
    "is_operator_intervention",
    "is_bug_fix_task",
    "LANDED_STATUSES",
    "BUG_FIX_HEURISTIC",
    "L2_THRESHOLD",
    "ALL_SECTIONS",
    "_print_brief",
    "_print_standard",
]


# Wave-5 merge-flow: TESTING is the resting shelf (merged, as good as done).
# DONE is the manual housekeeping flip. Both count as landed; CANCELED stays
# excluded. Every metric that used to filter on DONE re-checks against this set.
LANDED_STATUSES = frozenset({TaskStatus.DONE, TaskStatus.TESTING})


# Bug-fix heuristic (L2 ladder): title matches /\bfix\w*/ (case-insensitive).
# Matches fix / fixes / fixed / fixing as a standalone word. False-positive
# risks (prefix, suffix, affix) are blocked by the word boundary; we still
# state the heuristic plainly in the --ladder output so the choice is never
# a silent assumption.
BUG_FIX_HEURISTIC = "title matches /\\bfix\\w*/i (fix/fixes/fixed/fixing as a standalone word)"
_BUG_FIX_RE = re.compile(r"\bfix\w*", re.IGNORECASE)


# L2 ladder goal: 10 hands-free bug fixes in a row. State plainly when met.
L2_THRESHOLD = 10


ALL_SECTIONS = {"header", "autonomy", "touches", "cost", "timing", "by_spec", "ladder"}


# ── Classification ──────────────────────────────────────────────────


def classify_task(task):
    """Return a dict describing how this task was driven to completion.

    Keys:
      agent_authored     bool   — True iff no disqualifying operator touch
                                  occurred. A disqualifying touch is an
                                  operator transition INTO a terminal
                                  state (DONE / REVIEW / TESTING), which
                                  means the operator closed or promoted
                                  the task — the agent did not drive it
                                  across the finish line. Steering comments
                                  are recorded but not disqualifying: an
                                  operator directive the agent follows
                                  does not change authorship. This matches
                                  the wave-1 audit ("17/18 — only #104
                                  was hand-completed") where 8 steering
                                  comments and 4 hand-resolved merge
                                  conflicts were tracked separately as
                                  "operator unsticks" without removing the
                                  task from the agent-authored column.
      operator_touches   list   — every operator intervention we observed.
      dispatch_at        dt|None — first DISPATCH_STATUSES transition.
      done_at            dt|None — first status=DONE transition.
    """
    touches = []
    disqualifying = False

    # Pull history rows ordered ascending so we see dispatch before done.
    histories = list(
        TaskHistory.objects.filter(task_id=task.id).order_by("changed_at")
    )
    comments = list(
        TaskComment.objects.filter(task_id=task.id).order_by("created_at")
    )

    dispatch_at = None
    done_at = None
    landed_at = None

    for h in histories:
        if h.field_name == "status":
            new_val = (h.new_value or "").upper()
            if dispatch_at is None and new_val in DISPATCH_STATUSES:
                dispatch_at = h.changed_at
            if done_at is None and new_val == TaskStatus.DONE:
                done_at = h.changed_at
            if landed_at is None and new_val in LANDED_STATUSES:
                landed_at = h.changed_at
            if is_operator_email(h.changed_by):
                operator_drove_to_terminal = (
                    new_val in OPERATOR_TERMINAL_STATUSES
                )
                if operator_drove_to_terminal:
                    disqualifying = True
                touches.append({
                    "kind": "manual_transition",
                    "who": h.changed_by,
                    "when": h.changed_at,
                    "detail": f"{h.old_value} -> {h.new_value}",
                    "disqualifying": operator_drove_to_terminal,
                })

    for c in comments:
        if (
            (c.comment_type in STEERING_COMMENT_TYPES)
            and is_operator_email(c.author_email)
        ):
            # Steering comment is an operator directive the agent
            # follows; it does not change authorship. Track it for the
            # unsticks count, do not flag as disqualifying.
            touches.append({
                "kind": "steering_comment",
                "who": c.author_email,
                "when": c.created_at,
                "detail": (c.content or "")[:120],
                "disqualifying": False,
            })

    return {
        "task_id": task.id,
        "agent_authored": not disqualifying,
        "operator_touches": touches,
        "dispatch_at": dispatch_at,
        "done_at": done_at,
        "landed_at": landed_at,
    }


def _tokens_for_task(task):
    """Total tokens captured for a task (0 if missing)."""
    usage = compute_usage_from_trace(task) or {}
    return (
        usage.get("total_tokens")
        or (usage.get("input_tokens", 0) + usage.get("output_tokens", 0))
        or 0
    )


def is_operator_intervention(task):
    """True iff any operator action broke the hands-free path.

    Wave-5 zero-unstick definition: dispatch → TESTING with no operator
    intervention. Operator intervention = ANY operator-authored comment
    (any comment_type, not just steering) OR ANY operator manual status
    PATCH that is NOT the very first dispatch transition (TODO/whatever
    → EXECUTING/IN_PROGRESS). The initial dispatch PATCH does NOT count
    — operators routinely flip backlog→EXECUTING to kick a task off
    and the agent does the rest.

    Returns True when no dispatch was ever observed either: a task that
    landed without ever going through EXECUTING/IN_PROGRESS is, by
    definition, not hands-free (no agent work was triggered).

    Pure DB read; callable from any worker. Pulls histories + comments
    in two cheap queries.
    """
    histories = list(
        TaskHistory.objects.filter(task_id=task.id).order_by("changed_at")
    )
    comments = list(
        TaskComment.objects.filter(task_id=task.id).order_by("created_at")
    )

    # Any operator comment = intervention (stricter than the old rule,
    # which only flagged steering-type comments).
    if any(is_operator_email(c.author_email) for c in comments):
        return True

    # Walk status histories. The first DISPATCH_STATUSES transition is
    # the initial dispatch and does NOT count, even if the operator
    # performed it. Anything else by the operator is intervention.
    initial_dispatch_seen = False
    for h in histories:
        if h.field_name != "status":
            continue
        new_val = (h.new_value or "").upper()
        if (not initial_dispatch_seen) and new_val in DISPATCH_STATUSES:
            initial_dispatch_seen = True
            continue
        if is_operator_email(h.changed_by):
            return True

    # No dispatch ever happened — the task never reached the agent.
    if not initial_dispatch_seen:
        return True

    return False


def is_bug_fix_task(task):
    """True iff `task` looks like a bug fix by title heuristic.

    Heuristic: title matches /\\bfix\\w*/i — fix/fixes/fixed/fixing as a
    standalone word. Excludes prefix/suffix/affix (no word boundary before
    'fix'). State the heuristic plainly in the --ladder output so the
    choice is never a silent assumption.
    """
    return bool(_BUG_FIX_RE.search(task.title or ""))


def _landing_at(task):
    """First history timestamp where status entered LANDED_STATUSES.

    Used to sort landings chronologically for the L2 streak. For tasks
    resting on TESTING, that's the first EXECUTING→TESTING row; for
    tasks that flipped to DONE, it's whichever came first (TESTING is
    the resting shelf, DONE is the manual flip, so TESTING precedes
    DONE on a happy path). Returns None if no landing transition found.
    """
    landed_at = None
    landed_seen = False
    for h in TaskHistory.objects.filter(task_id=task.id).order_by("changed_at"):
        if h.field_name != "status":
            continue
        if (h.new_value or "").upper() in LANDED_STATUSES:
            landed_at = h.changed_at
            landed_seen = True
            break
    if landed_at is None:
        # Defensive fallback — task.status says LANDED but no history row
        # records the transition. Use the task's last_updated_at as a
        # soft fallback so the task still appears in chronological order.
        return getattr(task, "last_updated_at", None)
    return landed_at


# ── Aggregate metrics ───────────────────────────────────────────────


def compute_board_metrics(board=None, spec=None):
    """Compute autonomy metrics for a board, spec, or the whole DB.

    Filters: if board is given, scope to its tasks; if spec is given,
    scope further. Returns a flat dict ready for json/standard output.

    Wave-5: tasks in LANDED_STATUSES (DONE ∪ TESTING) are counted as
    landed. The resting shelf is TESTING; DONE is the manual flip.
    CANCELED stays excluded. Key naming reflects this: ``total_landed``
    replaces ``total_done`` from earlier waves — the two are equivalent
    on the wave-1/wave-2 fixture (all tasks went DONE→DONE) and diverge
    starting wave-5, where most landings rest on TESTING.
    """
    qs = Task.objects.all()
    if board is not None:
        qs = qs.filter(board=board)
    if spec is not None:
        qs = qs.filter(spec=spec)
    # Pull all matching tasks once; classify_task will refetch history
    # + comments per task (small N — wave audits run ~tens, not thousands).
    tasks = list(qs.select_related("assignee", "spec").order_by("id"))

    landed_tasks = [t for t in tasks if (t.status or "").upper() in LANDED_STATUSES]

    agent_authored = 0
    operator_touches_total = 0
    tasks_with_zero_unsticks = 0
    total_tokens = 0
    tasks_with_capture_gaps = 0
    exec_durations_seconds = []
    dispatch_to_landed_seconds = []

    per_spec = defaultdict(lambda: {
        "spec_id": None,
        "title": "",
        "total_landed": 0,
        "agent_authored": 0,
        "operator_touches": 0,
        "tasks_with_zero_unsticks": 0,
        "tokens": 0,
        "capture_gaps": 0,
    })

    for t in landed_tasks:
        cls = classify_task(t)
        agent = cls["agent_authored"]
        if agent:
            agent_authored += 1
        # Count ALL operator touches, including on agent-authored tasks —
        # a steering comment on an agent-authored task is still an unstick.
        operator_touches_total += len(cls["operator_touches"])
        # Wave-5 sharper zero-unstick: any operator comment OR any
        # operator manual status PATCH beyond initial dispatch.
        if not is_operator_intervention(t):
            tasks_with_zero_unsticks += 1

        tokens = _tokens_for_task(t)
        total_tokens += tokens
        if tokens == 0:
            tasks_with_capture_gaps += 1

        # Exec duration: from metadata.last_duration_ms (the agent's
        # own self-report). Fallback to dispatch->landed delta if absent.
        meta = t.metadata or {}
        exec_ms = meta.get("last_duration_ms") or 0
        landed_at = cls.get("landed_at") or _landing_at(t)
        if exec_ms:
            exec_durations_seconds.append(exec_ms / 1000.0)
        elif cls["dispatch_at"] and landed_at:
            exec_durations_seconds.append(
                (landed_at - cls["dispatch_at"]).total_seconds()
            )

        if cls["dispatch_at"] and landed_at:
            dispatch_to_landed_seconds.append(
                (landed_at - cls["dispatch_at"]).total_seconds()
            )

        if t.spec_id:
            key = t.spec_id
            row = per_spec[key]
            row["spec_id"] = t.spec_id
            row["title"] = (t.spec.title if t.spec_id else "")
            row["total_landed"] += 1
            if agent:
                row["agent_authored"] += 1
            # Count all touches, on agent-authored tasks too — an
            # unstick on an agent-authored task is still an unstick.
            row["operator_touches"] += len(cls["operator_touches"])
            if not is_operator_intervention(t):
                row["tasks_with_zero_unsticks"] += 1
            row["tokens"] += tokens
            if tokens == 0:
                row["capture_gaps"] += 1

    total_landed = len(landed_tasks)
    autonomy_rate = (agent_authored / total_landed) if total_landed else 0
    avg_tokens = (total_tokens / total_landed) if total_landed else 0

    exec_dur = {
        "min": min(exec_durations_seconds) if exec_durations_seconds else 0,
        "max": max(exec_durations_seconds) if exec_durations_seconds else 0,
        "p50": percentile(exec_durations_seconds, 50),
        "p90": percentile(exec_durations_seconds, 90),
    }
    d2l = {
        "min": min(dispatch_to_landed_seconds) if dispatch_to_landed_seconds else 0,
        "max": max(dispatch_to_landed_seconds) if dispatch_to_landed_seconds else 0,
        "p50": percentile(dispatch_to_landed_seconds, 50),
        "p90": percentile(dispatch_to_landed_seconds, 90),
    }

    return {
        "scope": {
            "board_id": board.id if board else None,
            "board_name": board.name if board else None,
            "spec_id": spec.id if spec else None,
        },
        "total_landed": total_landed,
        # Back-compat alias — analytics.py + downstream consumers that
        # haven't moved to "landed" yet. Same value; the rename is the
        # honest label, not a new metric.
        "total_done": total_landed,
        "agent_authored": agent_authored,
        "autonomy_rate": autonomy_rate,
        "operator_touches_total": operator_touches_total,
        "tasks_with_zero_unsticks": tasks_with_zero_unsticks,
        "tasks_with_capture_gaps": tasks_with_capture_gaps,
        "total_tokens": total_tokens,
        "avg_tokens_per_change": avg_tokens,
        "cost": {
            "total_tokens": total_tokens,
            "avg_tokens_per_change": avg_tokens,
            "median_tokens_per_change": percentile(
                [_tokens_for_task(t) for t in landed_tasks], 50
            ),
            "tasks_with_capture_gaps": tasks_with_capture_gaps,
            "cost_capture_honest": True,
        },
        "exec_duration_seconds": exec_dur,
        "exec_duration_minutes": {
            k: (v / 60.0 if v else 0) for k, v in exec_dur.items()
        },
        "dispatch_to_landed_seconds": d2l,
        # Back-compat alias for the same reason as total_done.
        "dispatch_to_done_seconds": d2l,
        "per_spec": sorted(per_spec.values(), key=lambda r: r["spec_id"]),
    }


def compute_ladder_metrics(board=None, spec=None):
    """L2 ladder: consecutive hands-free landings of bug-fix-class tasks.

    Heuristic: title matches /\\bfix\\w*/i (fix/fixes/fixed/fixing as a
    standalone word). Stated plainly in the output and the JSON payload
    so the heuristic is never a silent assumption.

    Returns a flat dict with:
      total_bug_fixes_landed   int   — bug-fix tasks that landed (DONE/TESTING)
      hands_free_bug_fixes     int   — subset with no operator intervention
      current_streak           int   — trailing run of hands-free landings
      best_streak              int   — max run anywhere in the sorted list
      l2_threshold             int   — L2 goal (10), exposed for callers
      l2_met                   bool  — current_streak >= l2_threshold
      heuristic                str   — the title pattern, stated plainly
    """
    qs = Task.objects.all()
    if board is not None:
        qs = qs.filter(board=board)
    if spec is not None:
        qs = qs.filter(spec=spec)
    landed_bug_fixes = [
        t for t in qs
        if (t.status or "").upper() in LANDED_STATUSES
        and is_bug_fix_task(t)
    ]

    # Sort by landing timestamp (first TESTING/DONE transition) so the
    # streak walks chronologically.
    landings = []
    for t in landed_bug_fixes:
        when = _landing_at(t)
        if when is None:
            continue
        landings.append((when, t, not is_operator_intervention(t)))
    landings.sort(key=lambda r: r[0])

    total = len(landings)
    hands_free_total = sum(1 for _, _, hf in landings if hf)

    # Walk runs of consecutive hands-free landings.
    best = 0
    current = 0
    for _, _, hands_free in landings:
        if hands_free:
            current += 1
            best = max(best, current)
        else:
            current = 0

    return {
        "total_bug_fixes_landed": total,
        "hands_free_bug_fixes": hands_free_total,
        "current_streak": current,
        "best_streak": best,
        "l2_threshold": L2_THRESHOLD,
        "l2_met": current >= L2_THRESHOLD,
        "heuristic": BUG_FIX_HEURISTIC,
    }


# ── Output formatting ───────────────────────────────────────────────


def _format_duration(seconds):
    """Compact human-readable duration."""
    if not seconds:
        return "-"
    s = float(seconds)
    if s < 60:
        return f"{s:.0f}s"
    if s < 3600:
        return f"{s/60:.1f}m"
    return f"{s/3600:.1f}h"


def _print_brief(m, ladder=None):
    scope = m["scope"]["board_name"] or f"board #{m['scope']['board_id']}"
    total = m["total_landed"]
    print(
        f"Autonomy on '{scope}': {total} landed | "
        f"agent-authored {m['agent_authored']}/{total} "
        f"({m['autonomy_rate']*100:.0f}%) | "
        f"{m['operator_touches_total']} operator touches | "
        f"{m['cost']['tasks_with_capture_gaps']}/{total} capture gaps"
        + (f" | ladder {ladder['current_streak']}/{ladder['l2_threshold']}" if ladder else "")
    )


def _print_standard(m, sections=None, ladder=None):
    scope = m["scope"]["board_name"] or (
        f"board #{m['scope']['board_id']}" if m["scope"]["board_id"] else "all boards"
    )
    print(f"\n{'=' * 70}")
    print(f"  AUTONOMY METRICS — {scope}")
    print(f"{'=' * 70}")

    if want_section("autonomy", sections):
        print(
            f"  Agent-authored merges: "
            f"{m['agent_authored']}/{m['total_landed']} "
            f"({m['autonomy_rate']*100:.1f}%)"
        )
        print(
            f"  Zero-unstick tasks (dispatch->landed, no operator): "
            f"{m['tasks_with_zero_unsticks']}/{m['total_landed']}"
        )

    if want_section("touches", sections):
        print(f"  Operator touches (total): {m['operator_touches_total']}")

    if want_section("cost", sections):
        c = m["cost"]
        print(f"  Total tokens (landed tasks): {c['total_tokens']:,}")
        print(f"  Avg tokens per merged change: {c['avg_tokens_per_change']:,.0f}")
        print(f"  Median tokens per merged change: {c['median_tokens_per_change']:,.0f}")
        gap_note = (
            "no gaps" if c["tasks_with_capture_gaps"] == 0
            else f"{c['tasks_with_capture_gaps']} tasks missing token capture"
        )
        print(f"  Cost capture: {gap_note}")

    if want_section("timing", sections):
        e = m["exec_duration_seconds"]
        d = m["dispatch_to_landed_seconds"]
        print(f"  Exec duration (s):    "
              f"min={_format_duration(e['min'])} "
              f"p50={_format_duration(e['p50'])} "
              f"p90={_format_duration(e['p90'])} "
              f"max={_format_duration(e['max'])}")
        print(f"  Dispatch->landed (s): "
              f"min={_format_duration(d['min'])} "
              f"p50={_format_duration(d['p50'])} "
              f"p90={_format_duration(d['p90'])} "
              f"max={_format_duration(d['max'])}")

    if want_section("by_spec", sections) and m["per_spec"]:
        print(f"  Per-spec breakdown:")
        for row in m["per_spec"]:
            print(
                f"    Spec #{row['spec_id']} {row['title'][:30]:<32} "
                f"landed={row['total_landed']:<3} "
                f"agent={row['agent_authored']:<3} "
                f"touches={row['operator_touches']:<3} "
                f"gaps={row['capture_gaps']}"
            )

    if want_section("ladder", sections) and ladder is not None:
        l2 = "MET" if ladder["l2_met"] else "NOT YET"
        print(f"  Ladder (hands-free bug-fix streak):")
        print(f"    heuristic: {ladder['heuristic']}")
        print(f"    bug fixes landed: {ladder['total_bug_fixes_landed']} "
              f"(hands-free: {ladder['hands_free_bug_fixes']})")
        print(f"    current streak: {ladder['current_streak']}")
        print(f"    best streak:    {ladder['best_streak']}")
        print(f"    L2 threshold ({ladder['l2_threshold']} in a row): {l2}")

    print()


# ── Entry point ─────────────────────────────────────────────────────


def main():
    # Extract --spec N before parse_args sees it (parse_args treats the
    # first non-flag arg as the positional id; we want --spec to claim
    # its argument).
    argv = list(sys.argv)
    spec_id = None
    if "--spec" in argv:
        i = argv.index("--spec")
        if i + 1 < len(argv):
            spec_id = int(argv[i + 1])
            # Strip --spec and its value so parse_args won't claim the
            # value as the positional board id.
            argv = argv[:i] + argv[i + 2:]
    sys.argv = argv

    positional, mode, sections = parse_args(argv, positional_name="board_id")

    board = None
    if positional:
        try:
            board = Board.objects.get(pk=positional)
        except Board.DoesNotExist:
            print(f"Board #{positional} not found.")
            sys.exit(1)

    spec = None
    if spec_id is not None:
        try:
            spec = Spec.objects.get(pk=spec_id)
        except Spec.DoesNotExist:
            print(f"Spec #{spec_id} not found.")
            sys.exit(1)
        if board is None:
            board = spec.board

    m = compute_board_metrics(board=board, spec=spec)
    ladder = compute_ladder_metrics(board=board, spec=spec)

    if mode == "json":
        merged = {**m, "ladder": ladder}
        print_json(merged)
        return
    if mode == "brief":
        _print_brief(m, ladder=ladder)
        return
    _print_standard(m, sections=sections, ladder=ladder)


if __name__ == "__main__":
    main()