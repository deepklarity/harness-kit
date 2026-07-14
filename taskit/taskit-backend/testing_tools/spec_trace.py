#!/usr/bin/env python
"""Spec execution trace diagnostic.

Full trace of a spec's execution: tasks, dependencies, timeline, runs, problems.

Usage:
    cd taskit/taskit-backend
    python testing_tools/spec_trace.py <spec_id>
    python testing_tools/spec_trace.py <spec_id> --brief
    python testing_tools/spec_trace.py <spec_id> --json
    python testing_tools/spec_trace.py <spec_id> --sections tasks,problems
"""
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _utils import (
    setup_django, parse_args, want_section,
    format_duration, format_tokens, extract_token_parts,
    print_json,
)

setup_django()

from tasks.models import Spec, Task, TaskComment, TaskHistory, TaskRun, TaskStatus, MergeAttempt, MistakeEntry  # noqa: E402
from tasks.pricing import compute_spec_merge_summary  # noqa: E402
from tasks.spec_story import build_spec_story, topological_sort  # noqa: E402

ALL_SECTIONS = {"header", "tasks", "deps", "timeline", "comments", "problems", "runs", "merges", "story", "mistakes"}

STATUS_SYMBOLS = {
    TaskStatus.DONE: "+",
    TaskStatus.REVIEW: "+",
    TaskStatus.TESTING: "+",
    TaskStatus.IN_PROGRESS: "~",
    TaskStatus.EXECUTING: "~",
    TaskStatus.FAILED: "x",
    TaskStatus.TODO: ".",
    TaskStatus.BACKLOG: ".",
    # CANCELED is terminal-neutral (fable task 192): the work item was
    # parked by an operator, not by the agent. Surface it with its own
    # glyph so the trace stays legible — distinct from FAILED's 'x'
    # and DONE/REVIEW/TESTING's '+'.
    TaskStatus.CANCELED: "-",
}

TERMINAL_STATUSES = {TaskStatus.DONE, TaskStatus.REVIEW, TaskStatus.TESTING}
ACTIVE_STATUSES = {TaskStatus.IN_PROGRESS, TaskStatus.EXECUTING}
# CANCELED is terminal-neutral — not in TERMINAL_STATUSES (so a downstream
# dep stays WAITING, not BLOCKED) and not in ACTIVE_STATUSES (so the
# stuck-detection never complains about a parked task).
STUCK_THRESHOLD_SECONDS = 600


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
    all_runs = list(TaskRun.objects.filter(task_id__in=task_ids).order_by("-started_at"))
    all_mistakes = list(MistakeEntry.objects.filter(spec=spec).order_by("-created_at", "-id"))
    problems = detect_problems(tasks, all_histories)

    # Aggregate token/duration stats
    agg_tokens = 0
    agg_duration = 0
    for t in tasks:
        total, _, _ = extract_token_parts(t)
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
            "mistake_count": len(all_mistakes),
        }
        if want_section("tasks", sections):
            data["tasks"] = []
            for t in topological_sort(tasks):
                total, inp, out = extract_token_parts(t)
                data["tasks"].append({
                    "id": t.id,
                    "title": t.title,
                    "status": t.status,
                    "agent": t.assignee.name if t.assignee else None,
                    "tokens": {"total": total, "input": inp, "output": out},
                    "duration_ms": (t.metadata or {}).get("last_duration_ms"),
                    "failure_class": (t.metadata or {}).get("failure_class"),
                    "depends_on": t.depends_on or [],
                })
        if want_section("comments", sections):
            data["comment_count"] = len(all_comments)
        if want_section("runs", sections):
            data["runs"] = [
                {
                    "task_id": r.task_id,
                    "run_token": r.run_token,
                    "state": r.state,
                    "pid": r.pid,
                    "sandbox_name": r.sandbox_name,
                    "started_at": r.started_at.isoformat(),
                    "last_heartbeat": r.last_heartbeat.isoformat(),
                    "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                }
                for r in all_runs
            ]
        if want_section("merges", sections):
            data["merges"] = compute_spec_merge_summary(tasks)
        if want_section("story", sections):
            data["story"] = build_spec_story(spec)["tasks"]
        print_json(data)
        return

    # ── Brief mode ──
    if mode == "brief":
        status_str = ", ".join(f"{count} {s}" for s, count in sorted(status_dist.items()))
        tok = f"{agg_tokens:,}" if agg_tokens else "-"
        dur = format_duration(agg_duration)
        prob = f"{len(problems)} problems" if problems else "ok"
        mistakes = f", {len(all_mistakes)} mistakes" if all_mistakes else ""
        print(f"Spec #{spec.id}: {len(tasks)} tasks ({status_str}) | {tok} tokens | {dur} | {prob}{mistakes}")
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
            fc = (t.metadata or {}).get("failure_class")
            fc_str = f" {{{fc}}}" if fc and t.status == TaskStatus.FAILED else ""
            print(f"  {symbol} {t.id:<4} {title:<28} {t.status:<14} {agent:<12} {duration:<9} {tokens:<8}{deps_str}{fc_str}")
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

    if want_section("runs", sections):
        if all_runs:
            print(f"  {'TASK RUNS':^66}")
            print(f"  {'-' * 66}")
            print(f"  {'Task':<6} {'Token':<10} {'State':<10} {'PID':<8} {'Started':<10} {'Heartbeat':<10}")
            print(f"  {'-' * 66}")
            for r in all_runs:
                started = r.started_at.strftime("%H:%M:%S")
                heartbeat = r.last_heartbeat.strftime("%H:%M:%S")
                print(
                    f"  {r.task_id:<6} {r.run_token[:8]:<10} {r.state:<10} "
                    f"{r.pid or '-':<8} {started:<10} {heartbeat:<10}"
                )
            print()

    if want_section("merges", sections):
        all_merges = list(MergeAttempt.objects.filter(task_id__in=task_ids).order_by("-started_at"))
        merge_summary = compute_spec_merge_summary(tasks)
        print(f"  {'MERGE ATTEMPTS':^66}")
        print(f"  {'-' * 66}")
        if all_merges:
            print(f"  {'Task':<6} {'Mode':<10} {'Outcome':<9} {'Trigger':<15} {'Lag':<8} {'Dur':<8}")
            print(f"  {'-' * 66}")
            for m in all_merges:
                lag = f"{(m.started_at - m.dispatched_at).total_seconds():.1f}s" if m.dispatched_at else "-"
                dur = (
                    f"{(m.finished_at - m.started_at).total_seconds():.1f}s"
                    if m.finished_at else "-"
                )
                print(f"  {m.task_id:<6} {m.mode:<10} {m.outcome:<9} {m.trigger:<15} {lag:<8} {dur:<8}")
            mean_lag = merge_summary["mean_dispatch_lag_seconds"]
            mean_lag_str = f"{mean_lag}s" if mean_lag is not None else "-"
            print(
                f"  attempts={merge_summary['attempt_count']} "
                f"static={merge_summary['static_count']} "
                f"agent={merge_summary['agent_count']} "
                f"human={merge_summary['human_assisted_count']} "
                f"cost=${merge_summary['merge_cost_usd']:.4f} "
                f"mean_lag={mean_lag_str}"
            )
        else:
            print("  none")
    if want_section("story", sections):
        story = build_spec_story(spec)
        print(f"  WAVE STORY")
        print(f"  {'-' * 66}")
        for t in story["tasks"]:
            dispatched = t["dispatched_at"].strftime("%H:%M:%S") if t["dispatched_at"] else "-"
            dur = format_duration(t["duration_ms"])
            tok = f"{t['tokens']['total']:,}" if t["tokens"]["total"] else "-"
            cost = f"${t['cost_usd']:.4f}" if t["cost_usd"] is not None else "-"
            merge = t["merge"]["mode"] if t["merge"] else "-"
            redo = t["redo_rounds"]["count"]
            print(
                f"  #{t['task_id']:<4} {t['title'][:24]:<24} {t['status']:<12} "
                f"{(t['agent'] or '-'):<10} dispatched={dispatched} dur={dur:<6} "
                f"tok={tok:<8} cost={cost:<8} redo={redo} merge={merge}"
            )
            comment = t["latest_comment"]
            print(f"      latest: {comment['headline'] if comment else '-'}")
            for g in t["gaps"]:
                print(f"      ! gap: {g}")
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

    if want_section("mistakes", sections):
        if all_mistakes:
            print(f"  {'MISTAKES LEDGER':^66}")
            print(f"  {'-' * 66}")
            for e in all_mistakes:
                cls = f" {{{e.failure_class}}}" if e.failure_class else ""
                verdict = f" [{e.verdict}]" if e.verdict else ""
                print(f"  #{e.task_id:<4} ({e.source}){verdict}{cls} {e.one_liner[:50]}")
            print()
        elif mode == "full":
            print(f"  MISTAKES LEDGER: none")
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
