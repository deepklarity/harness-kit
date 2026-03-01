#!/usr/bin/env python
"""Spec execution trace diagnostic.

Full trace of a spec's execution: tasks, dependencies, timeline, problems.

Usage:
    cd taskit/taskit-backend
    python testing_tools/spec_trace.py <spec_id>
    python testing_tools/spec_trace.py <spec_id> --brief
    python testing_tools/spec_trace.py <spec_id> --json
    python testing_tools/spec_trace.py <spec_id> --sections tasks,problems
"""
import sys
from collections import defaultdict
from datetime import datetime, timezone

from _utils import (
    setup_django, parse_args, want_section,
    format_duration, format_tokens, extract_token_parts,
    print_json,
)

setup_django()

from tasks.models import Spec, Task, TaskComment, TaskHistory, TaskStatus  # noqa: E402

ALL_SECTIONS = {"header", "tasks", "deps", "timeline", "comments", "problems"}

STATUS_SYMBOLS = {
    TaskStatus.DONE: "+",
    TaskStatus.REVIEW: "+",
    TaskStatus.TESTING: "+",
    TaskStatus.IN_PROGRESS: "~",
    TaskStatus.EXECUTING: "~",
    TaskStatus.FAILED: "x",
    TaskStatus.TODO: ".",
    TaskStatus.BACKLOG: ".",
}

TERMINAL_STATUSES = {TaskStatus.DONE, TaskStatus.REVIEW, TaskStatus.TESTING}
ACTIVE_STATUSES = {TaskStatus.IN_PROGRESS, TaskStatus.EXECUTING}
STUCK_THRESHOLD_SECONDS = 600


def topological_sort(tasks):
    """Sort tasks by dependency order (Kahn's algorithm). Falls back to ID order on cycles."""
    task_map = {str(t.id): t for t in tasks}
    in_degree = defaultdict(int)
    dependents = defaultdict(list)

    for t in tasks:
        tid = str(t.id)
        for dep in t.depends_on or []:
            if dep in task_map:
                in_degree[tid] += 1
                dependents[dep].append(tid)

    queue = [str(t.id) for t in tasks if in_degree[str(t.id)] == 0]
    result = []
    while queue:
        node = queue.pop(0)
        result.append(node)
        for dep_id in dependents[node]:
            in_degree[dep_id] -= 1
            if in_degree[dep_id] == 0:
                queue.append(dep_id)

    remaining = [str(t.id) for t in tasks if str(t.id) not in result]
    return [task_map[tid] for tid in result + remaining]


def detect_problems(tasks, all_histories):
    """Auto-detect problems. Returns list of problem strings.

    Uses pre-fetched histories to avoid N+1 queries.
    """
    now = datetime.now(timezone.utc)
    task_map = {str(t.id): t for t in tasks}
    # Group histories by task for O(1) lookup
    history_by_task = defaultdict(list)
    for h in all_histories:
        history_by_task[h.task_id].append(h)

    problems = []
    for t in tasks:
        # Stuck: active status with no recent history
        if t.status in ACTIVE_STATUSES:
            task_histories = history_by_task.get(t.id, [])
            if task_histories:
                last = max(task_histories, key=lambda h: h.changed_at)
                elapsed = (now - last.changed_at).total_seconds()
                if elapsed > STUCK_THRESHOLD_SECONDS:
                    problems.append(
                        f"Task #{t.id} stuck in {t.status} for {elapsed/60:.0f}m "
                        f"(last activity: {last.changed_at.strftime('%H:%M:%S')})"
                    )

        # Failed dep chains
        if t.status == TaskStatus.FAILED:
            for other in tasks:
                if other.depends_on and str(t.id) in other.depends_on:
                    if other.status not in {TaskStatus.FAILED, TaskStatus.DONE}:
                        problems.append(f"Task #{other.id} blocked by failed dep #{t.id}")

        # Executing with unmet deps
        if t.status in ACTIVE_STATUSES and t.depends_on:
            for dep_id in t.depends_on:
                dep_task = task_map.get(dep_id)
                if dep_task and dep_task.status not in TERMINAL_STATUSES:
                    problems.append(f"Task #{t.id} is {t.status} but dep #{dep_id} is {dep_task.status}")
                    break

        # No assignee
        if t.status in ACTIVE_STATUSES and not t.assignee_id:
            problems.append(f"Task #{t.id} is {t.status} but has no assignee")

    return problems


def trace_spec(spec_id, mode="standard", sections=None):
    """Trace a spec execution. Returns dict (json mode) or prints to stdout.

    Args:
        spec_id: Spec PK.
        mode: 'brief' | 'standard' | 'full' | 'json'.
        sections: Set of section names, or None for all.
                  Options: header, tasks, deps, timeline, comments, problems.
    """
    try:
        spec = Spec.objects.select_related("board").get(pk=spec_id)
    except Spec.DoesNotExist:
        print(f"Spec #{spec_id} not found.")
        return

    tasks = list(
        Task.objects.filter(spec=spec)
        .select_related("assignee")
        .prefetch_related("labels")
        .order_by("id")
    )
    if not tasks:
        print(f"Spec #{spec_id} has no tasks.")
        return

    task_ids = [t.id for t in tasks]

    # Pre-fetch all histories and comments once (used by multiple sections)
    all_histories = list(TaskHistory.objects.filter(task_id__in=task_ids).order_by("changed_at"))
    all_comments = list(TaskComment.objects.filter(task_id__in=task_ids).order_by("created_at"))
    problems = detect_problems(tasks, all_histories)

    # Aggregate token/duration stats
    agg_tokens = 0
    agg_duration = 0
    for t in tasks:
        total, _, _ = extract_token_parts(t.metadata)
        agg_tokens += total
        agg_duration += (t.metadata or {}).get("last_duration_ms", 0) or 0

    status_dist = {}
    for t in tasks:
        status_dist[t.status] = status_dist.get(t.status, 0) + 1

    # ── JSON mode ──
    if mode == "json":
        data = {
            "spec_id": spec.id,
            "odin_id": spec.odin_id,
            "title": spec.title,
            "board": {"id": spec.board.id, "name": spec.board.name},
            "task_count": len(tasks),
            "status_distribution": status_dist,
            "total_tokens": agg_tokens,
            "total_duration_ms": agg_duration,
            "problem_count": len(problems),
            "problems": problems,
        }
        if want_section("tasks", sections):
            data["tasks"] = []
            for t in topological_sort(tasks):
                total, inp, out = extract_token_parts(t.metadata)
                data["tasks"].append({
                    "id": t.id,
                    "title": t.title,
                    "status": t.status,
                    "agent": t.assignee.name if t.assignee else None,
                    "tokens": {"total": total, "input": inp, "output": out},
                    "duration_ms": (t.metadata or {}).get("last_duration_ms"),
                    "depends_on": t.depends_on or [],
                })
        if want_section("comments", sections):
            data["comment_count"] = len(all_comments)
        print_json(data)
        return

    # ── Brief mode ──
    if mode == "brief":
        status_str = ", ".join(f"{count} {s}" for s, count in sorted(status_dist.items()))
        tok = f"{agg_tokens:,}" if agg_tokens else "-"
        dur = format_duration(agg_duration)
        prob = f"{len(problems)} problems" if problems else "ok"
        print(f"Spec #{spec.id}: {len(tasks)} tasks ({status_str}) | {tok} tokens | {dur} | {prob}")
        if problems:
            for p in problems:
                print(f"  x {p}")
        return

    # ── Standard / Full mode ──
    if want_section("header", sections):
        print(f"\n{'=' * 70}")
        print(f"  SPEC TRACE: #{spec.id} - {spec.title}")
        print(f"{'=' * 70}")
        print(f"  ID:       {spec.id}")
        print(f"  Odin ID:  {spec.odin_id}")
        print(f"  Title:    {spec.title}")
        print(f"  Source:   {spec.source}")
        print(f"  Board:    {spec.board.name} (#{spec.board.id})")
        print(f"  Created:  {spec.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
        cwd = spec.metadata.get("working_dir", "-") if spec.metadata else "-"
        print(f"  CWD:      {cwd}")
        print()

    if want_section("tasks", sections):
        print(f"  {'TASK SUMMARY':^66}")
        print(f"  {'-' * 66}")
        print(f"  {'ID':<6} {'Title':<28} {'Status':<14} {'Agent':<12} {'Duration':<9} {'Tokens':<8}")
        print(f"  {'-' * 66}")

        for t in topological_sort(tasks):
            symbol = STATUS_SYMBOLS.get(t.status, "?")
            title = t.title[:26] + ".." if len(t.title) > 28 else t.title
            agent = "-"
            if t.assignee:
                agent = t.assignee.name[:10] + ".." if len(t.assignee.name) > 12 else t.assignee.name
            duration = format_duration((t.metadata or {}).get("last_duration_ms"))
            tokens = format_tokens(t.metadata)
            deps_str = ""
            if t.depends_on:
                dep_symbols = []
                task_map = {str(x.id): x for x in tasks}
                for dep_id in t.depends_on:
                    dep_task = task_map.get(dep_id)
                    if dep_task:
                        dep_symbols.append(f"{STATUS_SYMBOLS.get(dep_task.status, '?')}{dep_id}")
                deps_str = f" [{', '.join(dep_symbols)}]"
            print(f"  {symbol} {t.id:<4} {title:<28} {t.status:<14} {agent:<12} {duration:<9} {tokens:<8}{deps_str}")
        print()

    if want_section("deps", sections):
        task_map = {str(t.id): t for t in tasks}
        has_deps = [t for t in tasks if t.depends_on]
        if has_deps:
            print(f"  DEPENDENCY ANALYSIS")
            print(f"  {'-' * 66}")
            for t in has_deps:
                print(f"  Task #{t.id}: {t.title[:40]}")
                all_satisfied = True
                for dep_id in t.depends_on:
                    dep_task = task_map.get(dep_id)
                    if not dep_task:
                        print(f"    x #{dep_id} - NOT FOUND (external dependency)")
                        all_satisfied = False
                    elif dep_task.status in TERMINAL_STATUSES:
                        print(f"    + #{dep_id} - {dep_task.status}")
                    elif dep_task.status == TaskStatus.FAILED:
                        print(f"    x #{dep_id} - FAILED")
                        all_satisfied = False
                    else:
                        print(f"    ~ #{dep_id} - {dep_task.status}")
                        all_satisfied = False
                if all_satisfied and t.status in {TaskStatus.TODO, TaskStatus.BACKLOG}:
                    print(f"    >> STUCK: all deps satisfied but task is still {t.status}")
            print()

    if want_section("timeline", sections):
        task_map = {t.id: t for t in tasks}
        print(f"  EXECUTION TIMELINE")
        print(f"  {'-' * 66}")
        for h in all_histories:
            ts = h.changed_at.strftime("%H:%M:%S")
            task = task_map.get(h.task_id)
            task_label = f"Task {h.task_id}" + (f" ({task.title[:20]})" if task else "")
            if h.field_name == "created":
                desc = "created"
            elif h.field_name == "status":
                desc = f"status: {h.old_value} -> {h.new_value} (by {h.changed_by})"
            elif h.field_name == "assignee_id":
                desc = f"assigned: {h.old_value or 'none'} -> {h.new_value or 'none'} (by {h.changed_by})"
            else:
                desc = f"{h.field_name}: changed (by {h.changed_by})"
            print(f"  {ts} | {task_label:<30} | {desc}")
        print()

    if want_section("comments", sections):
        comments_by_task = defaultdict(list)
        for c in all_comments:
            comments_by_task[c.task_id].append(c)
        if comments_by_task:
            print(f"  EXECUTION COMMENTS")
            print(f"  {'-' * 66}")
            for t in tasks:
                task_comments = comments_by_task.get(t.id, [])
                if not task_comments:
                    continue
                print(f"  Task #{t.id} ({len(task_comments)} comments):")
                for c in task_comments:
                    ts = c.created_at.strftime("%H:%M:%S")
                    ct = getattr(c, "comment_type", "status_update")
                    author = c.author_label or c.author_email
                    lines = c.content.strip().split("\n")
                    summary = lines[0][:70]
                    if len(lines) > 1:
                        summary += f" (+{len(lines)-1} lines)"
                    print(f"    [{ts}] [{ct:15s}] {author}: {summary}")
                    # Full mode: show all comment content
                    if mode == "full":
                        for line in lines[1:]:
                            print(f"      {line}")
            print()

    if want_section("problems", sections):
        if not problems:
            print(f"  PROBLEMS: None detected +")
        else:
            print(f"  PROBLEMS DETECTED ({len(problems)})")
            print(f"  {'-' * 66}")
            for p in problems:
                print(f"  ! {p}")
        print()


def main():
    positional, mode, sections = parse_args(sys.argv, positional_name="spec_id")
    if not positional:
        print("Usage: python testing_tools/spec_trace.py <spec_id> [--brief|--full|--json] [--sections a,b,c]")
        print(f"\nSections: {', '.join(sorted(ALL_SECTIONS))}")
        sys.exit(1)

    trace_spec(positional, mode=mode, sections=sections)


if __name__ == "__main__":
    main()
