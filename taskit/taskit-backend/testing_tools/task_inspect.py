#!/usr/bin/env python
"""Deep single-task inspection diagnostic.

Shows everything about one task: metadata, history, comments, dependencies,
output parsing diagnosis, and automatic problem detection.

Usage:
    cd taskit/taskit-backend
    python testing_tools/task_inspect.py <task_id>
    python testing_tools/task_inspect.py <task_id> --brief
    python testing_tools/task_inspect.py <task_id> --json
    python testing_tools/task_inspect.py <task_id> --full
    python testing_tools/task_inspect.py <task_id> --sections basic,tokens,diagnosis
"""
import json
import sys

from _utils import (
    setup_django, parse_args, want_section,
    format_token_parts, extract_token_parts, format_duration,
    print_json,
)

setup_django()

from tasks.models import Task, TaskComment, TaskHistory  # noqa: E402

ALL_SECTIONS = {"basic", "deps", "metadata", "description", "history", "comments", "diagnosis"}


def diagnose(task, comments):
    """Auto-detect problems. Returns list of problem strings."""
    problems = []
    meta = task.metadata or {}

    if not meta.get("last_duration_ms"):
        problems.append("No duration recorded — execution_result may not have been posted")
    if not meta.get("last_usage"):
        problems.append("No token usage captured — agent metadata.usage was empty or not forwarded")

    full_output = meta.get("full_output", "")
    if full_output and full_output.strip().startswith("(node:"):
        problems.append("full_output starts with Node.js warnings — raw stdout, not extracted agent text")
    if full_output:
        lines = full_output.strip().splitlines()
        json_lines = sum(1 for l in lines if l.strip().startswith("{"))
        plain_lines = sum(1 for l in lines if l.strip() and not l.strip().startswith("{"))
        if json_lines > 0 and plain_lines > 0:
            problems.append("full_output has JSON mixed with plain text — extract_agent_text likely failed")
        elif json_lines > plain_lines:
            problems.append("full_output is mostly JSON — extract_agent_text likely fell through")

    for c in comments:
        ct = getattr(c, "comment_type", "")
        if ct == "telemetry" and ('{"type":' in c.content or '"delta":true' in c.content):
            problems.append(f"Telemetry comment #{c.id} contains raw JSON fragments")

    if task.status == "REVIEW" and not any(
        getattr(c, "comment_type", "") == "telemetry" for c in comments
    ):
        problems.append("Task in REVIEW but no telemetry comment — execution_result endpoint not called?")

    return problems


def inspect_task(task_id, mode="standard", sections=None):
    """Inspect a single task. Returns dict (json mode) or prints to stdout.

    Args:
        task_id: Task PK.
        mode: 'brief' | 'standard' | 'full' | 'json'.
        sections: Set of section names to include, or None for all.
                  Options: basic, deps, metadata, description, history, comments, diagnosis.
    """
    try:
        task = Task.objects.select_related("assignee", "spec", "board").get(pk=task_id)
    except Task.DoesNotExist:
        print(f"Task #{task_id} not found.")
        return

    meta = task.metadata or {}
    total, inp, out = extract_token_parts(meta)
    duration_ms = meta.get("last_duration_ms")
    comments = list(TaskComment.objects.filter(task_id=task.id).order_by("created_at"))
    problems = diagnose(task, comments)

    # ── JSON mode ──
    if mode == "json":
        data = {
            "id": task.id,
            "title": task.title,
            "status": task.status,
            "priority": task.priority,
            "board": {"id": task.board.id, "name": task.board.name},
            "spec_id": task.spec_id,
            "assignee": task.assignee.name if task.assignee else None,
            "model": task.model_name,
            "tokens": {"total": total, "input": inp, "output": out},
            "duration_ms": duration_ms,
            "problems": problems,
            "problem_count": len(problems),
        }
        if want_section("deps", sections):
            data["depends_on"] = task.depends_on or []
        if want_section("comments", sections):
            data["comment_count"] = len(comments)
            data["comment_types"] = list({getattr(c, "comment_type", "status_update") for c in comments})
        if want_section("history", sections):
            histories = TaskHistory.objects.filter(task_id=task.id).order_by("changed_at")
            data["history_count"] = histories.count()
        if want_section("metadata", sections):
            data["metadata"] = meta
        if want_section("description", sections):
            data["description"] = task.description
        print_json(data)
        return

    # ── Brief mode ──
    if mode == "brief":
        dur = format_duration(duration_ms)
        tok = f"{total:,}" if total else "-"
        prob = f"{len(problems)} problems" if problems else "ok"
        spec_label = f"spec #{task.spec_id}" if task.spec_id else "no spec"
        print(f"Task #{task.id}: {task.status} | {tok} tokens | {dur} | {prob} | {spec_label}")
        if problems:
            for p in problems:
                print(f"  x {p}")
        return

    # ── Standard / Full mode ──
    print(f"\n{'=' * 70}")
    print(f"  TASK INSPECT: #{task.id} - {task.title}")
    print(f"{'=' * 70}")

    if want_section("basic", sections):
        print(f"\n  BASIC INFO")
        print(f"  {'-' * 50}")
        print(f"  ID:          {task.id}")
        print(f"  Title:       {task.title}")
        print(f"  Status:      {task.status}")
        print(f"  Priority:    {task.priority}")
        print(f"  Board:       {task.board.name} (#{task.board.id})")
        print(f"  Spec:        {task.spec.title if task.spec else '-'} (#{task.spec_id or '-'})")
        print(f"  Assignee:    {task.assignee.name if task.assignee else '-'} (#{task.assignee_id or '-'})")
        print(f"  Model:       {task.model_name or '-'}")
        print(f"  Complexity:  {task.complexity or '-'}")
        print(f"  Created by:  {task.created_by}")
        print(f"  Created at:  {task.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  Updated at:  {task.last_updated_at.strftime('%Y-%m-%d %H:%M:%S')}")

    if want_section("deps", sections):
        print(f"\n  DEPENDENCIES")
        print(f"  {'-' * 50}")
        if task.depends_on:
            # Batch-fetch all dependency tasks in one query
            dep_tasks = {
                str(t.id): t
                for t in Task.objects.filter(pk__in=task.depends_on)
            }
            for dep_id in task.depends_on:
                dep = dep_tasks.get(str(dep_id))
                if dep:
                    print(f"  -> #{dep.id} {dep.title[:40]} [{dep.status}]")
                else:
                    print(f"  -> #{dep_id} NOT FOUND")
        else:
            print("  (none)")

        # Reverse deps
        dependents = Task.objects.filter(depends_on__contains=[str(task.id)])
        if dependents.exists():
            print(f"\n  DEPENDED ON BY")
            print(f"  {'-' * 50}")
            for d in dependents:
                print(f"  <- #{d.id} {d.title[:40]} [{d.status}]")

    if want_section("metadata", sections):
        print(f"\n  METADATA")
        print(f"  {'-' * 50}")
        if meta:
            if duration_ms:
                print(f"  duration:    {format_duration(duration_ms)}")
            print(f"  tokens:      {format_token_parts(meta)}")
            if meta.get("selected_model"):
                print(f"  exec model:  {meta['selected_model']}")
            if meta.get("working_dir"):
                print(f"  working_dir: {meta['working_dir']}")
            if meta.get("tmux_session"):
                print(f"  tmux:        {meta['tmux_session']}")
            # full_output diagnosis
            full_output = meta.get("full_output", "")
            if full_output:
                lines = full_output.strip().splitlines()
                json_lines = sum(1 for l in lines if l.strip().startswith("{"))
                plain_lines = sum(1 for l in lines if l.strip() and not l.strip().startswith("{"))
                print(f"  full_output: {len(full_output)} chars, {len(lines)} lines ({json_lines} JSON, {plain_lines} plain)")
            if meta.get("effective_input"):
                print(f"  eff. input:  {len(meta['effective_input'])} chars")
            # Remaining keys (standard: truncated, full: complete)
            skip_keys = {
                "last_duration_ms", "last_usage", "selected_model", "working_dir",
                "full_output", "effective_input", "tmux_session", "taskit_id", "started_at",
            }
            extra = {k: v for k, v in meta.items() if k not in skip_keys}
            max_val_len = None if mode == "full" else 100
            for key, value in extra.items():
                val_str = json.dumps(value, indent=2, default=str) if isinstance(value, (dict, list)) else str(value)
                if max_val_len and len(val_str) > max_val_len:
                    val_str = val_str[:max_val_len] + "..."
                print(f"  {key}: {val_str}")
        else:
            print("  (empty)")

    if want_section("description", sections):
        print(f"\n  DESCRIPTION")
        print(f"  {'-' * 50}")
        if task.description:
            limit = None if mode == "full" else 500
            desc = task.description if limit is None else task.description[:limit]
            if limit and len(task.description) > limit:
                desc += f"\n  ... ({len(task.description)} chars total)"
            for line in desc.split("\n"):
                print(f"  {line}")
        else:
            print("  (empty)")

    if want_section("history", sections):
        histories = TaskHistory.objects.filter(task_id=task.id).order_by("changed_at")
        print(f"\n  HISTORY ({histories.count()} entries)")
        print(f"  {'-' * 50}")
        for h in histories:
            ts = h.changed_at.strftime("%Y-%m-%d %H:%M:%S")
            if h.field_name == "created":
                print(f"  {ts} | created (by {h.changed_by})")
            elif h.field_name == "status":
                print(f"  {ts} | status: {h.old_value} -> {h.new_value} (by {h.changed_by})")
            elif h.field_name == "assignee_id":
                print(f"  {ts} | assignee: {h.old_value or 'none'} -> {h.new_value or 'none'} (by {h.changed_by})")
            else:
                trunc = None if mode == "full" else 50
                old_short = h.old_value if (trunc is None or len(h.old_value) <= trunc) else h.old_value[:trunc] + "..."
                new_short = h.new_value if (trunc is None or len(h.new_value) <= trunc) else h.new_value[:trunc] + "..."
                print(f"  {ts} | {h.field_name}: {old_short} -> {new_short} (by {h.changed_by})")

    if want_section("comments", sections):
        print(f"\n  COMMENTS ({len(comments)})")
        print(f"  {'-' * 50}")
        type_markers = {"status_update": ">>", "telemetry": "##", "question": "??", "reply": "<<"}
        max_lines = None if mode == "full" else 8
        for c in comments:
            ts = c.created_at.strftime("%Y-%m-%d %H:%M:%S")
            author = c.author_label or c.author_email
            ct = getattr(c, "comment_type", "status_update")
            marker = type_markers.get(ct, "  ")
            print(f"  {marker} [{ts}] [{ct}] {author}:")
            content_lines = c.content.split("\n")
            show_lines = content_lines if max_lines is None else content_lines[:max_lines]
            for line in show_lines:
                print(f"    {line}")
            if max_lines and len(content_lines) > max_lines:
                print(f"    ... ({len(content_lines) - max_lines} more lines)")
            print()

    if want_section("diagnosis", sections):
        print(f"  DIAGNOSIS")
        print(f"  {'-' * 50}")
        if not problems:
            print("  ok — no problems detected")
        else:
            for p in problems:
                print(f"  x {p}")

    print()


def main():
    positional, mode, sections = parse_args(
        sys.argv, positional_name="task_id"
    )
    if not positional:
        print("Usage: python testing_tools/task_inspect.py <task_id> [--brief|--full|--json] [--sections a,b,c]")
        print(f"\nSections: {', '.join(sorted(ALL_SECTIONS))}")
        sys.exit(1)

    inspect_task(positional, mode=mode, sections=sections)


if __name__ == "__main__":
    main()
