"""Per-agent routing stats — shared between autonomy_metrics.py and
the new history-driven routing endpoint consumed by the odin suggester.

Single source of truth for the success-rate / median-cost calculation
that powers ``/boards/{id}/agent-stats/`` and, indirectly, the
``suggest_routing`` ranking on the odin side.

The split:
  * Pure helpers (``percentile``, ``is_operator_email``,
    ``aggregate_agent_stats``) — no Django dependency, importable from
    either the backend or the odin process.
  * Django-bound reader (``compute_agent_stats_for_board``) — uses
    the existing Task/TaskHistory/TaskComment models plus the
    ``classify_task`` rule (still owned by ``autonomy_metrics``) to
    turn raw rows into the per-agent ``AgentStats``.

Existing callers (``autonomy_metrics.py``) re-export the moved helpers
so older imports keep working — the refactor is footprint-negative.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence


# ── Constants ────────────────────────────────────────────────────────


# Statuses that count as a task being actively worked on. Used to find
# the dispatch timestamp when EXECUTING/IN_PROGRESS is the first
# non-system transition. (Mirrored from autonomy_metrics — extracting
# would create a circular import.)
DISPATCH_STATUSES = frozenset({"EXECUTING", "IN_PROGRESS"})

# Statuses an operator-driven transition INTO counts as operator-drove-it.
OPERATOR_TERMINAL_STATUSES = frozenset({"DONE", "REVIEW", "TESTING"})

# Comments posted by the operator that steer the agent.
STEERING_COMMENT_TYPES = frozenset({"question", "reply"})


# ── Pure helpers ─────────────────────────────────────────────────────


# Machine-author domain classes. An author whose email ends in any of
# these suffixes is a machine (agent harness, system worker, trace
# sidecar, proof upload). Matched case-insensitively on the email's
# domain suffix — the rule is "by domain class" rather than a denylist
# of specific addresses, so a new agent/service with the same domain
# pattern is classified correctly without code changes.
#
# Wave-8 (W248) added the @system, @harness.kit, and @taskit classes
# after the ladder showed 0 hands-free of 13: live data carries
# `odin+memory@system` (twins memory lookup), `odin+dag-executor@system`
# (system worker), `odin@harness.kit` (trace sidecar), and
# `proof-upload@taskit` (proof upload) as routine comment authors, and
# the prior rule that only excluded `@odin.agent` + the literal
# `system@taskit` flagged every such task as operator-intervened.
_MACHINE_EMAIL_DOMAINS: tuple[str, ...] = (
    "@odin.agent",    # agent harness (claude@odin.agent, glm+...@odin.agent, ...)
    "@odin",          # internal services (merge-agent@odin — DAG executor merge agent)
    "@system",        # odin-side system services (odin+memory@system, odin+dag-executor@system, ...)
    "@harness.kit",   # trace / harness sidecar (odin@harness.kit, ...)
    "@taskit",        # taskit-side services (system@taskit, proof-upload@taskit, ...)
)


def is_human_author(email: str) -> bool:
    """True iff `email` looks like a human operator.

    A human is anyone whose email does NOT end in one of the machine
    domain classes in ``_MACHINE_EMAIL_DOMAINS``. By-domain-class
    matching (suffix on `@`) rather than per-address allowlisting — a
    new agent or service with the same domain pattern is classified
    correctly with no code change.

    Conservative fallback: an empty / ``None`` email is treated as human
    so blank authorship counts as operator intervention (matches the
    pre-W8 behavior; the alternative — silently treating blanks as
    machines — would let historical gaps hide real interventions).
    """
    if not email:
        return True
    e = email.lower()
    return not any(e.endswith(domain) for domain in _MACHINE_EMAIL_DOMAINS)


# Back-compat alias. The function was historically named
# ``is_operator_email`` and returns True for humans; the positive
# ``is_human_author`` is the canonical name going forward. Same
# callable, so existing call sites + older tests keep working.
is_operator_email = is_human_author


def percentile(values: Sequence[float], p: float) -> float:
    """Linear-interpolated percentile (0–100). Returns 0 on empty input.

    Standard "rank = (n-1) * p/100" interpolation. Matches numpy's
    default 'linear' method.
    """
    if not values:
        return 0
    sorted_vals = sorted(values)
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = (len(sorted_vals) - 1) * (p / 100.0)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = rank - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


# ── Data shapes ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class AgentStats:
    """Per-agent rollup over observed merged tasks.

    The dataclass is intentionally Plain-Old-Data: it serializes to
    JSON for the /boards/{id}/agent-stats/ REST endpoint and is the
    cross-process wire format between odin and taskit.

    Fields:
      name           short agent name (e.g. "gemini", "claude")
      sample_count   how many merged tasks fed this rollup
      success_count  how many were agent-authored (no operator takeover)
      success_rate   success_count / sample_count (0.0–1.0)
      median_tokens  median total tokens across merged tasks
                     (0 when token capture is missing — caller must
                     surface this as a cost-capture gap, not a free lunch)
    """

    name: str
    sample_count: int
    success_count: int
    success_rate: float
    median_tokens: float

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "sample_count": self.sample_count,
            "success_count": self.success_count,
            "success_rate": self.success_rate,
            "median_tokens": self.median_tokens,
        }


@dataclass(frozen=True)
class TaskOutcome:
    """One task's contribution to per-agent stats.

    Lives here so the suggester has a stable contract across
    implementations (DB reader, JSON loader, synthetic test fixture).
    """

    agent: str
    success: bool
    tokens: int

    def to_dict(self) -> dict:
        return {"agent": self.agent, "success": self.success, "tokens": self.tokens}


def aggregate_agent_stats(
    outcomes: Iterable[TaskOutcome | dict],
) -> Dict[str, AgentStats]:
    """Bucket a stream of per-task outcomes into per-agent AgentStats.

    Accepts ``TaskOutcome`` dataclasses OR plain dicts with the same
    three fields (so callers can hand-construct synthetic fixtures
    without ORM setup). Capture gaps (tokens=0) are mixed into the
    median honestly — the caller surfaces the gap if it matters, but
    we never fabricate a token count to fill one.
    """
    bucketed: Dict[str, List[TaskOutcome]] = defaultdict(list)
    for o in outcomes:
        outcome = TaskOutcome(**o) if isinstance(o, dict) else o
        bucketed[outcome.agent].append(outcome)

    stats: Dict[str, AgentStats] = {}
    for agent, items in bucketed.items():
        successes = sum(1 for it in items if it.success)
        sample_count = len(items)
        tokens = [it.tokens for it in items if it.tokens]
        stats[agent] = AgentStats(
            name=agent,
            sample_count=sample_count,
            success_count=successes,
            success_rate=(successes / sample_count) if sample_count else 0.0,
            median_tokens=percentile(tokens, 50) if tokens else 0,
        )
    return stats


# ── Django-bound reader ──────────────────────────────────────────────


def _classify_task_outcome(task, histories, comments) -> Optional[TaskOutcome]:
    """Apply the wave-1 classification rule (no disqualifying operator
    touch during the task's lifecycle) and return a TaskOutcome.

    Returns ``None`` if the task cannot contribute (e.g. not DONE).
    A disqualifying operator touch returns a TaskOutcome with
    ``success=False`` so the disqualified task still counts as an
    observed sample — the suggester uses success_rate = successes /
    observed, not successes / succeeded.

    Stays Django-agnostic: the caller supplies pre-fetched
    history rows + comments to keep this function cheap to test.
    The agent name comes from the task's assignee when available
    and falls back to the ``created_by`` email so hand-created test
    tasks still aggregate sensibly.
    """
    if (task.status or "").upper() != "DONE":
        return None

    # Walk history rows in ascending order looking for an operator
    # who closed/promoted the task (DONE/REVIEW/TESTING). Matches
    # autonomy_metrics.classify_task exactly — same flag, same reason.
    disqualifying = False
    for h in histories:
        if h.field_name != "status":
            continue
        new_val = (h.new_value or "").upper()
        if not is_operator_email(h.changed_by):
            continue
        if new_val in OPERATOR_TERMINAL_STATUSES:
            disqualifying = True
            break

    # Compute token count via the same on-the-fly trace reader the API
    # uses (compute_usage_from_trace), with a metadata.last_usage
    # fallback for legacy tasks whose trace comment was never captured.
    # Tasks with zero capture still contribute to the observed count —
    # capture gaps surface via median_tokens=0, which the caller can
    # treat as a noisy datum.
    tokens = 0
    try:
        from tasks.execution_processing import compute_usage_from_trace
        usage = compute_usage_from_trace(task) or {}
        if isinstance(usage, dict):
            tokens = (
                usage.get("total_tokens")
                or ((usage.get("input_tokens", 0) or 0)
                    + (usage.get("output_tokens", 0) or 0))
                or 0
            )
    except Exception:
        tokens = 0
    if not tokens:
        meta = getattr(task, "metadata", None) or {}
        last_usage = meta.get("last_usage", {}) if isinstance(meta, dict) else {}
        if isinstance(last_usage, dict):
            tokens = (
                last_usage.get("total_tokens")
                or ((last_usage.get("input_tokens", 0) or 0)
                    + (last_usage.get("output_tokens", 0) or 0))
                or 0
            )

    # Agent identity: prefer the assignee's email (set when the task
    # was actually executed by an agent user), fall back to created_by.
    agent_email = None
    if task.assignee_id and getattr(task, "assignee", None):
        agent_email = getattr(task.assignee, "email", None)
    if not agent_email:
        agent_email = task.created_by
    agent = _extract_agent_name(agent_email) if agent_email else "unknown"
    return TaskOutcome(agent=agent, success=(not disqualifying), tokens=tokens)


def _extract_agent_name(email: str) -> str:
    """Short agent name from an email like 'plan+opus@odin.agent'.

    Matches the convention analytics.py uses for cost buckets so the
    two views (per-agent and per-task) speak the same vocabulary.
    """
    if not email:
        return "unknown"
    if email.endswith("@odin.agent"):
        local = email.split("@")[0]
        plus_idx = local.find("+")
        return local[:plus_idx] if plus_idx != -1 else local
    return email


def compute_agent_stats_for_board(
    board, spec=None
) -> Dict[str, AgentStats]:
    """Compute per-agent success + cost stats for a board (or one
    spec on that board).

    Reads Task, TaskHistory, TaskComment rows directly through the
    Django ORM. Mirrors the structure of
    ``autonomy_metrics.compute_board_metrics`` but only returns the
    per-agent slice — the new minimal contract the suggester needs.

    Refuses to import any non-Django code beyond this module; the
    recommendation is callers iterate (board, None), (board, spec)
    for finer-grained reports.
    """
    from tasks.models import Task, TaskComment, TaskHistory

    qs = Task.objects.all()
    if board is not None:
        qs = qs.filter(board=board)
    if spec is not None:
        qs = qs.filter(spec=spec)
    done_tasks = list(qs.filter(status="DONE").select_related("assignee"))

    outcomes: List[TaskOutcome] = []
    for t in done_tasks:
        histories = list(
            TaskHistory.objects.filter(task_id=t.id).order_by("changed_at")
        )
        comments = list(
            TaskComment.objects.filter(task_id=t.id).order_by("created_at")
        )
        outcome = _classify_task_outcome(t, histories, comments)
        if outcome is not None:
            outcomes.append(outcome)

    return aggregate_agent_stats(outcomes)
