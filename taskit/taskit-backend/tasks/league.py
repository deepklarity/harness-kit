"""Per-(agent, model) league table — ranks agent+model pairs over
landed tasks (DONE ∪ TESTING). Mirrors the structure of
``tasks.agent_stats`` (shared helpers: ``is_operator_email``,
``percentile``).

Split:
  * Pure helpers (``LeagueRow``, ``aggregate_league_rows``) — no Django
    dependency, importable from anywhere.
  * Django-bound reader (``compute_league_for_board``) — uses Task,
    TaskHistory, MergeAttempt, plus ``compute_usage_from_trace`` and
    ``estimate_task_cost`` to fold per-task snapshots into
    per-(agent, model) rows.

Single source of truth rule (task 188): reuse agent_stats helpers; do
NOT build a second aggregation of "operator-touched the task".
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional

from .agent_stats import is_operator_email, percentile


LANDED_STATUSES = ("DONE", "TESTING")


# ── Data shapes ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class LeagueRow:
    """One row of the league table — per (agent, model) pair.

    Field semantics:
      agent, model          key pair (grouping axis)
      tasks_landed          sum of landed tasks
      hands_free_count      sum of landed tasks without operator takeover
      hands_free_pct        hands_free_count / tasks_landed
      redo_rounds_avg       mean of per-task rework counts
      tokens_median         median of per-task total tokens
      duration_ms_median    median of per-task last_duration_ms
      merge_conflicts_caused sum of tasks with any conflict MergeAttempt
      cost_usd_total        sum of per-task estimated costs (USD)
    """

    agent: str
    model: str
    tasks_landed: int
    hands_free_count: int
    hands_free_pct: float
    redo_rounds_avg: float
    tokens_median: float
    duration_ms_median: float
    merge_conflicts_caused: int
    cost_usd_total: float

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "model": self.model,
            "tasks_landed": self.tasks_landed,
            "hands_free_count": self.hands_free_count,
            "hands_free_pct": self.hands_free_pct,
            "redo_rounds_avg": self.redo_rounds_avg,
            "tokens_median": self.tokens_median,
            "duration_ms_median": self.duration_ms_median,
            "merge_conflicts_caused": self.merge_conflicts_caused,
            "cost_usd_total": self.cost_usd_total,
        }


# ── Pure aggregation ─────────────────────────────────────────────────


def aggregate_league_rows(rows: list[dict]) -> List[LeagueRow]:
    """Group per-task dict rows by (agent, model) into LeagueRow.

    Each input row carries the 10 LeagueRow fields for one task
    snapshot (typically tasks_landed=1). Sums/means/medians are applied
    per the field's semantics (see LeagueRow docstring).
    """
    if not rows:
        return []

    grouped: Dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        key = (r["agent"], r["model"])
        grouped[key].append(r)

    out: List[LeagueRow] = []
    for (agent, model), items in grouped.items():
        tasks_landed = sum(int(r["tasks_landed"]) for r in items)
        hands_free_count = sum(int(r["hands_free_count"]) for r in items)
        hands_free_pct = (
            hands_free_count / tasks_landed if tasks_landed else 0.0
        )
        redo_rounds_avg = (
            sum(float(r["redo_rounds_avg"]) for r in items) / len(items)
        )
        tokens_list = [float(r["tokens_median"]) for r in items]
        tokens_median = percentile(tokens_list, 50) if tokens_list else 0.0
        duration_list = [float(r["duration_ms_median"]) for r in items]
        duration_ms_median = (
            percentile(duration_list, 50) if duration_list else 0.0
        )
        merge_conflicts_caused = sum(
            int(r["merge_conflicts_caused"]) for r in items
        )
        cost_usd_total = sum(float(r["cost_usd_total"]) for r in items)

        out.append(LeagueRow(
            agent=agent,
            model=model,
            tasks_landed=tasks_landed,
            hands_free_count=hands_free_count,
            hands_free_pct=hands_free_pct,
            redo_rounds_avg=redo_rounds_avg,
            tokens_median=tokens_median,
            duration_ms_median=duration_ms_median,
            merge_conflicts_caused=merge_conflicts_caused,
            cost_usd_total=cost_usd_total,
        ))

    out.sort(key=lambda r: (-r.tasks_landed, r.agent, r.model))
    return out


# ── Django-bound reader ──────────────────────────────────────────────


def _was_operator_takeover(histories) -> bool:
    """True if any history row shows an operator closing/promoting the
    task to a terminal status (DONE/REVIEW/TESTING). Mirrors the
    disqualifying-operator rule in ``agent_stats._classify_task_outcome``.
    """
    for h in histories:
        if h.field_name != "status":
            continue
        if not is_operator_email(h.changed_by):
            continue
        new_val = (h.new_value or "").upper()
        if new_val in ("DONE", "REVIEW", "TESTING"):
            return True
    return False


def _resolve_agent(task) -> str:
    """Resolve agent name for the league grouping key.

    Falls back to ``unknown`` when there is no assignee — a deliberate
    departure from agent_stats, which falls back to created_by. League
    rows track agent+model execution pairs; an unassigned landed task
    has no execution identity, so it goes into the ``unknown`` bucket
    rather than masquerading as whatever user created it.
    """
    if task.assignee_id and getattr(task, "assignee", None):
        name = getattr(task.assignee, "name", None)
        if name:
            return name
    return "unknown"


def _resolve_model(task) -> str:
    """Resolve model name for the league grouping key.

    Resolution: task.model_name → metadata.selected_model →
    metadata.model → ``unknown``.
    """
    if task.model_name:
        return task.model_name
    md = getattr(task, "metadata", None) or {}
    return md.get("selected_model") or md.get("model") or "unknown"


def _tokens_for_task(task, usage: dict) -> int:
    """Total tokens, preferring on-the-fly trace usage; falls back to
    metadata.last_usage for legacy tasks with no trace comment.
    """
    tokens = (
        usage.get("total_tokens")
        or ((usage.get("input_tokens", 0) or 0)
            + (usage.get("output_tokens", 0) or 0))
        or 0
    )
    if tokens:
        return tokens
    md = getattr(task, "metadata", None) or {}
    last_usage = md.get("last_usage", {}) or {}
    if not isinstance(last_usage, dict):
        return 0
    return (
        last_usage.get("total_tokens")
        or ((last_usage.get("input_tokens", 0) or 0)
            + (last_usage.get("output_tokens", 0) or 0))
        or 0
    )


def _cost_for_task(model: str, usage: dict) -> float:
    """Estimated USD cost for one task; 0 when model isn't priced.
    """
    if model == "unknown":
        return 0.0
    from .pricing import estimate_task_cost

    inp = usage.get("input_tokens") or 0
    out = usage.get("output_tokens") or 0
    cost_val = estimate_task_cost(model, inp, out)
    return float(cost_val) if cost_val is not None else 0.0


def compute_league_for_board(
    board,
    *,
    since_spec: Optional[str] = None,
) -> List[LeagueRow]:
    """Compute per-(agent, model) league rows for one board.

    Reads Task rows where status is in ``LANDED_STATUSES``
    (DONE ∪ TESTING). When ``since_spec`` is provided, only tasks
    whose spec was created at or after the named spec are included
    (wave-comparable windows — operators name newer waves with
    reverse-alphabetic prefixes so the same lookup also filters by
    spec name). If ``since_spec`` does not match any spec on the
    board, the function returns an empty list.

    For each task:
      * agent    = ``assignee.name`` (else ``"unknown"``)
      * model    = ``task.model_name`` → ``metadata.selected_model``
                    → ``metadata.model`` → ``"unknown"``
      * success  = NOT operator-takeover (status flipped to a terminal
                    by a human email)
      * tokens   = ``compute_usage_from_trace(task)`` total, falling
                    back to ``metadata.last_usage``
      * duration_ms = ``metadata.last_duration_ms`` (0 if missing)
      * rework_count = ``metadata.rework_count`` (0 if missing)
      * merge_conflict = any MergeAttempt for this task has
                          outcome="conflict"
      * cost     = ``estimate_task_cost(model, in, out)``; 0 when None

    Rows are sorted by (tasks_landed desc, agent asc, model asc).
    """
    from .models import MergeAttempt, MergeOutcome, Spec, Task, TaskHistory
    from .execution_processing import compute_usage_from_trace

    qs = Task.objects.filter(board=board, status__in=LANDED_STATUSES)
    if since_spec:
        cutoff = Spec.objects.filter(
            board=board, odin_id=since_spec,
        ).order_by("created_at").first()
        if cutoff is None:
            return []
        qs = qs.filter(spec__created_at__gte=cutoff.created_at)

    tasks = list(qs.select_related("assignee"))
    task_ids = [t.id for t in tasks]

    histories_by_task: Dict[int, list] = defaultdict(list)
    if task_ids:
        for h in TaskHistory.objects.filter(task_id__in=task_ids).order_by(
            "changed_at"
        ):
            histories_by_task[h.task_id].append(h)

    conflict_task_ids = set()
    if task_ids:
        conflict_task_ids = set(
            MergeAttempt.objects.filter(
                task_id__in=task_ids,
                outcome=MergeOutcome.CONFLICT,
            ).values_list("task_id", flat=True)
        )

    rows: list[dict] = []
    for task in tasks:
        agent = _resolve_agent(task)
        model = _resolve_model(task)

        histories = histories_by_task.get(task.id, [])
        success = not _was_operator_takeover(histories)

        usage = compute_usage_from_trace(task) or {}
        if not isinstance(usage, dict):
            usage = {}

        tokens = _tokens_for_task(task, usage)

        md = getattr(task, "metadata", None) or {}
        duration_ms = int(md.get("last_duration_ms", 0) or 0)
        rework_count = int(md.get("rework_count", 0) or 0)

        merge_conflict = task.id in conflict_task_ids
        cost = _cost_for_task(model, usage)

        rows.append({
            "agent": agent,
            "model": model,
            "tasks_landed": 1,
            "hands_free_count": 1 if success else 0,
            "hands_free_pct": 1.0 if success else 0.0,
            "redo_rounds_avg": float(rework_count),
            "tokens_median": float(tokens),
            "duration_ms_median": float(duration_ms),
            "merge_conflicts_caused": 1 if merge_conflict else 0,
            "cost_usd_total": cost,
        })

    return aggregate_league_rows(rows)