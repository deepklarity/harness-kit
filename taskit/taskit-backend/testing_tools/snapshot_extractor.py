#!/usr/bin/env python
"""Extract a full spec run as a JSON snapshot for golden-file testing.

Dumps spec, tasks, comments, history, and execution metadata into structured
JSON files for regression testing.

Usage:
    cd taskit/taskit-backend
    python testing_tools/snapshot_extractor.py <spec_id> [output_dir]
    python testing_tools/snapshot_extractor.py <spec_id> [output_dir] --slim

    --slim: Exclude large text fields (description, content, full_output,
            effective_input) to produce smaller snapshots. Useful when you only
            need structural invariants, not full content.
"""
import json
import os
import sys
from datetime import datetime

from _utils import setup_django, serialize_datetime

setup_django()

from tasks.models import Spec, Task, TaskComment, TaskHistory  # noqa: E402


def extract_spec(spec, slim=False):
    data = {
        "id": spec.id,
        "odin_id": spec.odin_id,
        "title": spec.title,
        "source": spec.source,
        "abandoned": spec.abandoned,
        "board_id": spec.board_id,
        "board_name": spec.board.name,
        "metadata": spec.metadata,
        "created_at": spec.created_at.isoformat(),
    }
    if not slim:
        data["content"] = spec.content
    return data


def extract_task(task, slim=False):
    data = {
        "id": task.id,
        "title": task.title,
        "status": task.status,
        "priority": task.priority,
        "complexity": task.complexity,
        "model_name": task.model_name,
        "board_id": task.board_id,
        "spec_id": task.spec_id,
        "depends_on": task.depends_on,
        "created_by": task.created_by,
        "assignee_id": task.assignee_id,
        "assignee_email": task.assignee.email if task.assignee else None,
        "assignee_name": task.assignee.name if task.assignee else None,
        "dev_eta_seconds": task.dev_eta_seconds,
        "label_ids": list(task.labels.values_list("id", flat=True)),
        "label_names": list(task.labels.values_list("name", flat=True)),
        "created_at": task.created_at.isoformat(),
        "last_updated_at": task.last_updated_at.isoformat(),
    }
    if slim:
        # Keep metadata but strip the large text blobs from it
        meta = dict(task.metadata) if task.metadata else {}
        meta.pop("full_output", None)
        meta.pop("effective_input", None)
        data["metadata"] = meta
    else:
        data["description"] = task.description
        data["metadata"] = task.metadata
    return data


def extract_comment(comment, slim=False):
    data = {
        "id": comment.id,
        "task_id": comment.task_id,
        "author_email": comment.author_email,
        "author_label": comment.author_label,
        "comment_type": comment.comment_type,
        "created_at": comment.created_at.isoformat(),
    }
    if slim:
        # Include content length and first line for identification, not full body
        content = comment.content or ""
        first_line = content.split("\n")[0][:100] if content else ""
        data["content_length"] = len(content)
        data["content_preview"] = first_line
        data["attachments"] = bool(comment.attachments)
    else:
        data["content"] = comment.content
        data["attachments"] = comment.attachments
    return data


def extract_history(history):
    return {
        "id": history.id,
        "task_id": history.task_id,
        "field_name": history.field_name,
        "old_value": history.old_value,
        "new_value": history.new_value,
        "changed_at": history.changed_at.isoformat(),
        "changed_by": history.changed_by,
    }


def build_snapshot(spec_id, slim=False):
    spec = Spec.objects.select_related("board").get(pk=spec_id)
    tasks = list(
        Task.objects.filter(spec=spec)
        .select_related("assignee")
        .prefetch_related("labels")
        .order_by("id")
    )
    task_ids = [t.id for t in tasks]
    comments = list(TaskComment.objects.filter(task_id__in=task_ids).order_by("created_at"))
    history = list(TaskHistory.objects.filter(task_id__in=task_ids).order_by("changed_at"))

    # Derive summary stats
    statuses = [t.status for t in tasks]
    total_tokens = 0
    total_input_tokens = 0
    total_output_tokens = 0
    total_duration_ms = 0
    harnesses_used = set()
    for t in tasks:
        md = t.metadata or {}
        usage = md.get("last_usage", {})
        if usage:
            total_tokens += usage.get("total_tokens", 0) or 0
            total_input_tokens += usage.get("input_tokens", 0) or 0
            total_output_tokens += usage.get("output_tokens", 0) or 0
        dur = md.get("last_duration_ms")
        if dur:
            total_duration_ms += dur
        if t.assignee:
            harnesses_used.add(t.assignee.name)
        if t.model_name:
            harnesses_used.add(t.model_name)

    snapshot = {
        "_meta": {
            "extracted_at": datetime.now().isoformat(),
            "extractor": "testing_tools/snapshot_extractor.py",
            "spec_id": spec.id,
            "task_ids": task_ids,
            "slim": slim,
            "description": f"Golden snapshot of spec '{spec.title}' (#{spec.id})",
        },
        "summary": {
            "task_count": len(tasks),
            "comment_count": len(comments),
            "history_count": len(history),
            "status_distribution": {s: statuses.count(s) for s in set(statuses)},
            "total_tokens": total_tokens,
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_duration_ms": round(total_duration_ms, 1),
            "harnesses_used": sorted(harnesses_used),
            "has_dependencies": any(t.depends_on for t in tasks),
            "dependency_tasks": [t.id for t in tasks if t.depends_on],
        },
        "spec": extract_spec(spec, slim=slim),
        "tasks": [extract_task(t, slim=slim) for t in tasks],
        "comments": [extract_comment(c, slim=slim) for c in comments],
        "history": [extract_history(h) for h in history],
    }

    return snapshot


def write_snapshot(snapshot, output_dir):
    os.makedirs(output_dir, exist_ok=True)

    full_path = os.path.join(output_dir, "snapshot.json")
    with open(full_path, "w") as f:
        json.dump(snapshot, f, indent=2, default=serialize_datetime)
    print(f"  Written: {full_path}")

    for key in ["spec", "tasks", "comments", "history", "summary"]:
        piece_path = os.path.join(output_dir, f"{key}.json")
        with open(piece_path, "w") as f:
            json.dump(snapshot[key], f, indent=2, default=serialize_datetime)
        print(f"  Written: {piece_path}")

    # Write a README for the snapshot
    meta = snapshot["_meta"]
    summary = snapshot["summary"]
    readme_path = os.path.join(output_dir, "README.md")
    with open(readme_path, "w") as f:
        f.write(f"# Snapshot: {snapshot['spec']['title']}\n\n")
        f.write(f"- **Spec ID**: {meta['spec_id']}\n")
        f.write(f"- **Task IDs**: {meta['task_ids']}\n")
        f.write(f"- **Extracted**: {meta['extracted_at']}\n")
        f.write(f"- **Slim**: {meta['slim']}\n")
        f.write(f"- **Tasks**: {summary['task_count']}\n")
        f.write(f"- **Comments**: {summary['comment_count']}\n")
        f.write(f"- **History entries**: {summary['history_count']}\n")
        f.write(f"- **Harnesses**: {', '.join(summary['harnesses_used'])}\n")
        f.write(f"- **Total tokens**: {summary['total_tokens']:,}\n")
        f.write(f"- **Total duration**: {summary['total_duration_ms']:.0f}ms\n")
        f.write(f"- **Statuses**: {summary['status_distribution']}\n\n")
        f.write("## Files\n\n")
        f.write("| File | Contents |\n")
        f.write("|------|----------|\n")
        f.write("| `snapshot.json` | Full snapshot (all data) |\n")
        f.write("| `spec.json` | Spec metadata |\n")
        f.write("| `tasks.json` | All tasks with metadata |\n")
        f.write("| `comments.json` | All comments |\n")
        f.write("| `history.json` | All status/field mutations |\n")
        f.write("| `summary.json` | Aggregate stats |\n")
    print(f"  Written: {readme_path}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python testing_tools/snapshot_extractor.py <spec_id> [output_dir] [--slim]")
        print()
        print("  spec_id    - The taskit Spec PK (integer)")
        print("  output_dir - Where to write files (default: ./snapshots/spec_<id>)")
        print("  --slim     - Exclude large text fields for smaller snapshots")
        sys.exit(1)

    slim = "--slim" in sys.argv
    # Parse positional args (skip flags)
    positionals = [a for a in sys.argv[1:] if not a.startswith("--")]
    spec_id = positionals[0]
    output_dir = positionals[1] if len(positionals) > 1 else f"./snapshots/spec_{spec_id}"

    try:
        snapshot = build_snapshot(spec_id, slim=slim)
    except Spec.DoesNotExist:
        print(f"Spec #{spec_id} not found.")
        sys.exit(1)

    label = " (slim)" if slim else ""
    print(f"\nExtracting snapshot{label} for spec #{spec_id}: {snapshot['spec']['title']}")
    print(f"  Tasks: {snapshot['summary']['task_count']}")
    print(f"  Comments: {snapshot['summary']['comment_count']}")
    print(f"  History: {snapshot['summary']['history_count']}")
    print()

    write_snapshot(snapshot, output_dir)
    print(f"\nDone. Snapshot written to {output_dir}/")


if __name__ == "__main__":
    main()
