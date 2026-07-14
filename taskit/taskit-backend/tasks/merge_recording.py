"""Records ``MergeAttempt`` rows for the merge ladder (task #209).

The merge ladder is: static git merge first (cheap, no model) -> merge
agent only on conflict (model/tokens/cost) -> human only when the agent
can't resolve. This module is the single place that writes MergeAttempt
rows, called from the three dispatch points in ``dag_executor.py``
(reflection-pass merge, watchdog retry, human-guided resume) so the
ladder's shape stays visible without re-deriving it from call sites.
"""
from .models import MergeAttempt, MergeMode, MergeOutcome


def _resolve_mode(trigger, merge_result):
    if trigger == "human_resume":
        return MergeMode.HUMAN_ASSISTED
    if getattr(merge_result, "resolved_files", None) or getattr(merge_result, "ambiguous_files", None):
        return MergeMode.AGENT
    return MergeMode.STATIC


def _resolve_outcome(merge_result):
    if merge_result.success:
        return MergeOutcome.MERGED
    if merge_result.conflict:
        return MergeOutcome.CONFLICT
    return MergeOutcome.ERROR


def record_merge_attempt(task, trigger, merge_result, started_at, finished_at, dispatched_at=None):
    """Write one MergeAttempt row for a completed merge-ladder rung.

    ``started_at``/``finished_at`` must be captured by the caller around
    the actual merge call so the recorded duration reflects the attempt,
    not this bookkeeping call. ``dispatched_at`` is the time the merge was
    queued (``task.metadata['merge_dispatched_at']`` for the auto/retry
    paths, the human reply's ``created_at`` for the resume path) — omit it
    when unknown rather than guessing.
    """
    mode = _resolve_mode(trigger, merge_result)
    outcome = _resolve_outcome(merge_result)
    return MergeAttempt.objects.create(
        task=task,
        spec=task.spec,
        trigger=trigger,
        mode=mode,
        outcome=outcome,
        dispatched_at=dispatched_at,
        started_at=started_at,
        finished_at=finished_at,
        conflicting_files=list(getattr(merge_result, "conflicting_files", None) or []),
        agent_model=getattr(merge_result, "agent_model", None) or "",
        token_usage=getattr(merge_result, "token_usage", None) or {},
        error_message=merge_result.error or "",
    )
