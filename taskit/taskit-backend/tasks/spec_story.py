"""Wave story assembly — one source of truth shared by the
``GET /specs/<id>/story/`` endpoint (tasks/views.py) and the CLI
diagnostic (testing_tools/spec_trace.py --sections story).

Turns a spec's tasks into a task-ordered narrative: dispatch time,
agent+model, redo rounds (reflection verdicts), merge mode and conflicts,
tokens/cost, duration, current status, and the newest human-relevant
comment per task. Missing data is reported as an explicit gap rather
than hidden or silently defaulted — a row is never dropped for lack of
data (pre-ledger merges, tasks without cost capture, etc).
"""
from collections import defaultdict

from .execution_processing import compute_usage_from_trace
from .models import CommentType, ReflectionStatus, TaskHistory, TaskStatus
from .pricing import compute_task_estimated_cost

# Comment types that carry a deliberate human-relevant signal regardless of
# who posted them — a REFLECTION comment is the reviewer's verdict even
# though it's authored by an agent address.
_SIGNAL_COMMENT_TYPES = {CommentType.REFLECTION, CommentType.QUESTION, CommentType.PROMOTION_REPORT}

_MERGE_MODE_LABELS = {
    "merged": "clean merge",
    "noop": "no changes to merge",
    "conflict": "conflict — auto-resolution failed",
    "needs_human": "ambiguous conflict — escalated to human",
    "error": "merge error",
    "pending": "not yet merged",
}

# Statuses where a task has passed through the merge step, so a missing
# merge_status is worth flagging as a gap rather than expected/normal.
_MERGE_EXPECTED_STATUSES = {TaskStatus.TESTING, TaskStatus.DONE}


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


def _dispatch_time(histories):
    """First transition into an active status — the durable 'dispatched at' stamp.

    ``metadata["active_execution"]`` is popped once execution finishes, so
    it can't be trusted for completed tasks; TaskHistory rows are durable.
    """
    for h in histories:
        if h.field_name == "status" and h.new_value in (TaskStatus.EXECUTING, TaskStatus.IN_PROGRESS):
            return h.changed_at
    return None


def _merge_summary(metadata):
    merge_status = metadata.get("merge_status")
    if not merge_status:
        return None
    return {
        "status": merge_status,
        "mode": _MERGE_MODE_LABELS.get(merge_status, merge_status),
        "auto_resolved": bool(metadata.get("merge_agent_resolved")),
        "conflicting_files": metadata.get("merge_ambiguous_files") or [],
        "error": metadata.get("merge_error") or "",
        "diff_stat": metadata.get("diff_stat") or "",
    }


def _redo_rounds(reflections):
    completed = [r for r in reflections if r.status == ReflectionStatus.COMPLETED]
    return {
        "count": len(completed),
        "verdicts": [
            {
                "id": r.id,
                "verdict": r.verdict or None,
                "reviewer_agent": r.reviewer_agent,
                "reviewer_model": r.reviewer_model,
                "created_at": r.created_at,
            }
            for r in reflections
        ],
    }


def _newest_relevant_comment(comments):
    """Newest reviewer verdict or operator note for a task.

    Priority: a REFLECTION/QUESTION/PROMOTION_REPORT comment (a deliberate
    signal regardless of author) or any human-authored comment (author
    email not on the ``@odin.agent`` domain) beats routine agent
    status_update chatter. Falls back to the single newest comment of any
    kind so a task is never left without a headline just because it only
    has agent chatter.
    """
    ordered = sorted(comments, key=lambda c: c.created_at, reverse=True)
    for c in ordered:
        if c.comment_type in _SIGNAL_COMMENT_TYPES or not c.author_email.endswith("@odin.agent"):
            return c
    return ordered[0] if ordered else None


def _comment_entry(comment):
    if comment is None:
        return None
    lines = comment.content.strip().split("\n", 1)
    return {
        "id": comment.id,
        "comment_type": comment.comment_type,
        "author": comment.author_label or comment.author_email,
        "created_at": comment.created_at,
        "headline": lines[0][:200] if lines else "",
    }


def build_task_story(task, comments, reflections, histories):
    """Assemble one task's story entry. Pure function over pre-fetched data."""
    metadata = task.metadata or {}
    usage = compute_usage_from_trace(task) or {}
    input_tokens = usage.get("input_tokens") or 0
    output_tokens = usage.get("output_tokens") or 0
    total_tokens = usage.get("total_tokens") or (input_tokens + output_tokens)
    cost_usd = compute_task_estimated_cost(task, usage=usage) if total_tokens else None
    dispatched_at = _dispatch_time(histories)
    merge = _merge_summary(metadata)
    redo = _redo_rounds(reflections)
    comment = _newest_relevant_comment(comments)

    gaps = []
    if dispatched_at is None:
        gaps.append("no dispatch time recorded (pre-history or never dispatched)")
    if not total_tokens:
        gaps.append("no token/cost capture for this task")
    elif cost_usd is None:
        gaps.append("tokens captured but model pricing unknown — cost not computed")
    if merge is None and task.status in _MERGE_EXPECTED_STATUSES:
        gaps.append("no merge record (pre-ledger merge or merge step skipped)")
    if comment is None:
        gaps.append("no comments on this task")

    return {
        "task_id": task.id,
        "title": task.title,
        "status": task.status,
        "agent": task.assignee.name if task.assignee else None,
        "model": task.model_name or metadata.get("selected_model") or None,
        "dispatched_at": dispatched_at,
        "duration_ms": metadata.get("last_duration_ms"),
        "tokens": {
            "total": total_tokens,
            "input": input_tokens,
            "output": output_tokens,
        },
        "cost_usd": cost_usd,
        "redo_rounds": redo,
        "merge": merge,
        "latest_comment": _comment_entry(comment),
        "depends_on": task.depends_on or [],
        "gaps": gaps,
    }


def build_spec_story(spec):
    """Assemble the full wave story for a spec, in task (dependency) order."""
    from .models import ReflectionReport, Task, TaskComment

    tasks = list(
        Task.objects.filter(spec=spec)
        .select_related("assignee")
        .order_by("id")
    )
    task_ids = [t.id for t in tasks]

    comments_by_task = defaultdict(list)
    for c in TaskComment.objects.filter(task_id__in=task_ids):
        comments_by_task[c.task_id].append(c)

    reflections_by_task = defaultdict(list)
    for r in ReflectionReport.objects.filter(task_id__in=task_ids).order_by("created_at"):
        reflections_by_task[r.task_id].append(r)

    histories_by_task = defaultdict(list)
    for h in TaskHistory.objects.filter(task_id__in=task_ids).order_by("changed_at"):
        histories_by_task[h.task_id].append(h)

    ordered_tasks = topological_sort(tasks)
    task_stories = [
        build_task_story(
            t,
            comments_by_task.get(t.id, []),
            reflections_by_task.get(t.id, []),
            histories_by_task.get(t.id, []),
        )
        for t in ordered_tasks
    ]

    return {
        "spec_id": spec.id,
        "odin_id": spec.odin_id,
        "title": spec.title,
        "task_count": len(tasks),
        "tasks": task_stories,
    }
