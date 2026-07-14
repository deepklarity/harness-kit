"""Per-class failure-policy table (W4.4).

The failure-class tagger (``failure_tagger``) labels failures but a human
still decided requeue/hold — pure toil for transient classes (seen live:
a zai TLS stream error killed a task; the operator requeued it by hand
minutes later).  Root problem: classification exists, policy doesn't.

This module adds the **policy layer**: per-class action (auto-requeue,
reassign, human), bounded retries, backoff, and an operator-visible
audit comment per automatic decision.

Design rules
------------

- Extend (do not replace) the existing infra auto-redispatch machinery
  in ``tasks.dag_executor``.  No parallel code path: ``apply_failure_policy``
  delegates to ``_maybe_auto_redispatch_infra_failure`` for the
  AUTO_REQUEUE action and writes the same counter / history metadata.
- The quota-reassign path lives in ``tasks.views._maybe_reassign_on_quota_failure``
  and runs at reflection time — the REASSIGN policy here only marks the
  intent and posts the comment; the actual reassign is unchanged.
- HUMAN actions never auto-retry, never auto-reassign — they leave the
  task FAILED and post an audit comment so the operator can see WHY
  nothing auto-fired.
- Unknown / unclassified → human.  Auto-retrying an unclassified
  failure is worse than paging the operator.
- Counter / history / cap reuse the legacy keys (``auto_redispatch_count``,
  ``auto_redispatch_history``) so the audit trail is one stream, not two.
- Settings override retry bounds per class via
  ``DAG_EXECUTOR_FAILURE_POLICY_OVERRIDES`` (a dict mapping
  failure_class → {``max_retries``/``backoff_seconds``}).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any, Dict, Mapping, Optional

from django.utils import timezone


# ── Action constants ────────────────────────────────────────────────
ACTION_AUTO_REQUEUE = "auto_requeue"
ACTION_REASSIGN = "reassign"
ACTION_HUMAN = "human"

# The only actions an operator may assign to a class via the settings UI.
# Guards the board-editable ``failure_actions[cls].action`` override so a
# malformed value falls back to the class default instead of dispatching to
# an unknown branch.
_VALID_ACTIONS = frozenset({ACTION_AUTO_REQUEUE, ACTION_REASSIGN, ACTION_HUMAN})


# ── Standing preference order (task #328) ───────────────────────────
# The deliberate agent preference used when a protocol/infra failure has
# exhausted same-agent retries and must move to a same-tier ROUTING PEER.
# Cheap-and-capable first (glm/minimax), agy where it's fine, claude/codex
# reserved for firepower. This is the *default*; a board overrides it via
# ``Board.routing_policy['preference_order']`` — Default First.
DEFAULT_PREFERENCE_ORDER = ["glm", "minimax", "agy", "codex", "claude"]

# Default number of review rejections (COMPLETED NEEDS_WORK/FAIL reflection
# reports) before a *capability* failure escalates exactly one deliberate
# tier. The first rejection keeps the same agent (rework continuity); the
# second triggers the one-tier escalation.
DEFAULT_CAPABILITY_ESCALATE_AFTER = 2


# ── Metadata keys (mirror the legacy infra-redispatch keys so the
#    audit trail is one stream) ────────────────────────────────────
POLICY_META_KEY = "auto_redispatch_count"
POLICY_HISTORY_KEY = "auto_redispatch_history"
POLICY_HISTORY_MAX = 10
POLICY_CAP_REACHED_KEY = "policy_cap_reached"


@dataclass(frozen=True)
class FailurePolicy:
    """The automatic action to take when a task enters FAILED for a class.

    Attributes
    ----------
    action:
        One of ``ACTION_AUTO_REQUEUE`` (retry with same assignee),
        ``ACTION_REASSIGN`` (delegate to the existing reflection-time
        reassign path), ``ACTION_HUMAN`` (surface for a human).
    max_retries:
        Bound on automatic retries for ``AUTO_REQUEUE``.  0 for non-retry
        actions.
    backoff_seconds:
        Minimum delay between retries (the dispatcher stamps
        ``metadata.next_retry_after`` so a downstream scheduler can
        honour the delay).  0 = retry immediately.
    description:
        One-sentence summary of what the action does — embedded in
        every automatic audit comment.
    """

    action: str
    max_retries: int
    backoff_seconds: float
    description: str
    # task #328: for a protocol/infra AUTO_REQUEUE class, after the
    # same-agent retries in ``max_retries`` are exhausted, route to a
    # same-tier ROUTING PEER (never a tier jump) once before holding for a
    # human. False = exhaust retries then hold (the pre-#328 behaviour).
    peer_fallback: bool = False


# ── Default policy table ────────────────────────────────────────────
# Single source of truth for the per-class action.  Every value in
# ``failure_tagger.FAILURE_CLASSES`` has an entry — the table is the
# complete policy contract.

DEFAULT_POLICY_TABLE: Dict[str, FailurePolicy] = {
    # ── Transient infra — auto-requeue with same assignee ─────────
    "stale_execution": FailurePolicy(
        action=ACTION_AUTO_REQUEUE,
        max_retries=2,
        backoff_seconds=0,
        description="Worker died / no Odin runner recorded — retry with same assignee.",
        peer_fallback=True,
    ),
    "truncation": FailurePolicy(
        action=ACTION_AUTO_REQUEUE,
        max_retries=2,
        backoff_seconds=0,
        description="Provider truncated mid-generation — retry with same assignee.",
        peer_fallback=True,
    ),
    "silent_hang": FailurePolicy(
        action=ACTION_AUTO_REQUEUE,
        max_retries=2,
        backoff_seconds=0,
        description="Zero-output hang — retry with same assignee.",
        peer_fallback=True,
    ),
    "transport_error": FailurePolicy(
        action=ACTION_AUTO_REQUEUE,
        max_retries=2,
        backoff_seconds=30,
        description="Network/TLS/stream error — retry with same assignee after backoff.",
        peer_fallback=True,
    ),
    "lock_race": FailurePolicy(
        action=ACTION_AUTO_REQUEUE,
        max_retries=2,
        backoff_seconds=5,
        description="SQLite 'database is locked' — retry with same assignee after brief backoff.",
        peer_fallback=True,
    ),
    "sandbox_unavailable": FailurePolicy(
        # Task #331 — the sandbox/runtime never started an agent. No agent
        # ran, so no agent is blamed and no escalation fires; retry the
        # infra failure with the same assignee after a brief backoff (the
        # sandbox host may just be transiently saturated / booting).
        action=ACTION_AUTO_REQUEUE,
        max_retries=2,
        backoff_seconds=30,
        description="Sandbox/runtime never started an agent — infra retry with same assignee after backoff.",
    ),
    "error_loop": FailurePolicy(
        # Task #262 — the reconciler detected a provider retry loop in
        # the trace tail.  AUTO_REQUEUE with same assignee; the detector
        # stamps a provider-specific next_retry_after (10 min for
        # stream/quota classes) BEFORE this policy fires, and
        # backoff_seconds=0 here means _auto_requeue will NOT overwrite
        # that stamp (its guard skips the write when backoff is 0).
        action=ACTION_AUTO_REQUEUE,
        max_retries=2,
        backoff_seconds=0,
        description="Provider retry loop detected in trace — kill, wait out the window, requeue.",
        peer_fallback=True,
    ),

    # ── Quota → reassign (existing reflection-time path) ─────────
    "quota_exhaustion": FailurePolicy(
        action=ACTION_REASSIGN,
        max_retries=0,
        backoff_seconds=0,
        description="Quota exhausted — reassign to fallback provider on reflection.",
    ),

    # ── Real failures → human ────────────────────────────────────
    "env_missing": FailurePolicy(
        action=ACTION_HUMAN,
        max_retries=0,
        backoff_seconds=0,
        description="API key / auth / CLI missing — operator must fix environment.",
    ),
    "timeout": FailurePolicy(
        action=ACTION_HUMAN,
        max_retries=0,
        backoff_seconds=0,
        description="Execution exceeded deadline — operator must investigate.",
    ),
    "worktree_isolation": FailurePolicy(
        action=ACTION_HUMAN,
        max_retries=0,
        backoff_seconds=0,
        description="Worktree creation failed — operator must fix board config.",
    ),
    "disk_exhaustion": FailurePolicy(
        action=ACTION_HUMAN,
        max_retries=0,
        backoff_seconds=0,
        description="Disk full — operator must reclaim space.",
    ),
    "crash": FailurePolicy(
        action=ACTION_HUMAN,
        max_retries=0,
        backoff_seconds=0,
        description="Subprocess crash / unhandled exception — operator must investigate.",
    ),
    "cancelled": FailurePolicy(
        action=ACTION_HUMAN,
        max_retries=0,
        backoff_seconds=0,
        description="User-initiated stop — no automatic action.",
    ),
    "model_unavailable": FailurePolicy(
        action=ACTION_HUMAN,
        max_retries=0,
        backoff_seconds=0,
        description="Model not supported — operator must fix routing.",
    ),
    "unknown": FailurePolicy(
        action=ACTION_HUMAN,
        max_retries=0,
        backoff_seconds=0,
        description="Unclassified failure — operator must triage.",
    ),
}


# Fallback policy when metadata is missing or the class is unrecognised.
# Human is the safe default — auto-retrying an unclassified failure is
# worse than paging the operator.
_HUMAN_FALLBACK = DEFAULT_POLICY_TABLE["unknown"]


def _board_failure_action_overrides(board) -> Dict[str, Mapping[str, Any]]:
    """Per-class overrides drawn from ``board.routing_policy`` (task #328).

    The board is the single editable override layer surfaced in the web
    settings UI. Shape: ``routing_policy['failure_actions'] = {<class>:
    {max_retries: int, backoff_seconds: float}}``. Empty / missing →
    ``{}`` (use built-in defaults — Default First). Never raises.
    """
    policy_cfg = getattr(board, "routing_policy", None) or {}
    if not isinstance(policy_cfg, Mapping):
        return {}
    actions = policy_cfg.get("failure_actions") or {}
    return actions if isinstance(actions, Mapping) else {}


def resolve_policy(
    metadata: Mapping[str, Any],
    overrides: Optional[Mapping[str, Mapping[str, Any]]] = None,
    board=None,
) -> FailurePolicy:
    """Look up the policy for ``metadata['failure_class']``.

    Missing key → ``unknown`` policy (human).  Unrecognised class →
    ``unknown`` policy (human).  ``overrides`` is a per-class dict of
    field overrides (e.g. ``{"transport_error": {"max_retries": 5}}``)
    applied AFTER the default lookup.  Used by the settings knob
    ``DAG_EXECUTOR_FAILURE_POLICY_OVERRIDES``.

    When ``overrides`` is ``None`` (the default), the resolver reads
    the override table from ``settings.DAG_EXECUTOR_FAILURE_POLICY_OVERRIDES``
    so the test contract (callers don't need to pass settings explicitly)
    matches the production contract.

    task #328: ``board`` layers the per-board ``routing_policy`` overrides
    ON TOP of the settings overrides, so an edit in the web settings UI
    changes dispatch behaviour with no restart. The board is the
    single, visible, editable override surface; settings remain the
    host-level default.
    """
    cls = (metadata or {}).get("failure_class")
    policy = DEFAULT_POLICY_TABLE.get(cls) if cls else _HUMAN_FALLBACK
    if policy is None:
        policy = _HUMAN_FALLBACK

    if overrides is None:
        from django.conf import settings
        overrides = getattr(settings, "DAG_EXECUTOR_FAILURE_POLICY_OVERRIDES", None) or {}

    # Layer settings overrides, then per-board routing_policy overrides
    # (board wins — it's the operator-facing, live-editable surface).
    # ``action`` is overridable so the FULL per-class policy is editable in
    # settings (not just the retry cap), but only the three sanctioned
    # actions are accepted — a garbage value can never corrupt dispatch.
    valid_fields = {"max_retries", "backoff_seconds", "action"}
    layers = [overrides]
    if board is not None:
        layers.append(_board_failure_action_overrides(board))
    for layer in layers:
        if layer and cls in layer:
            safe = {}
            for k, v in dict(layer[cls] or {}).items():
                if k not in valid_fields:
                    continue
                if k == "action" and v not in _VALID_ACTIONS:
                    continue
                safe[k] = v
            if safe:
                policy = replace(policy, **safe)

    return policy


def effective_preference_order(board) -> list:
    """The board's routing peer preference order, or the default (task #328)."""
    policy_cfg = getattr(board, "routing_policy", None) or {}
    if isinstance(policy_cfg, Mapping):
        order = policy_cfg.get("preference_order")
        if isinstance(order, (list, tuple)) and order:
            return [str(a).strip().lower() for a in order if str(a).strip()]
    return list(DEFAULT_PREFERENCE_ORDER)


def capability_escalate_after(board) -> int:
    """Review rejections before a capability failure escalates one tier."""
    policy_cfg = getattr(board, "routing_policy", None) or {}
    if isinstance(policy_cfg, Mapping):
        val = policy_cfg.get("capability_escalate_after")
        try:
            n = int(val)
            if n >= 1:
                return n
        except (TypeError, ValueError):
            pass
    return DEFAULT_CAPABILITY_ESCALATE_AFTER


@dataclass(frozen=True)
class CapabilityPolicy:
    """The resolved capability-escalation decision for a board (task #328).

    Capability failure = repeated review rejection. The deliberate one-tier
    escalation is the ONLY sanctioned tier jump, and — like every other
    routing decision — it must flow through this single engine rather than a
    flag read ad-hoc at the call site.

    Attributes
    ----------
    enabled:
        Whether capability escalation may fire at all.
    escalate_after:
        Review rejections before the one-tier jump fires (>= 1).
    max_escalations:
        Cap on how many times a single task may escalate.
    """

    enabled: bool
    escalate_after: int
    max_escalations: int


def capability_policy(board) -> CapabilityPolicy:
    """Single authority for the capability-escalation decision (task #328).

    Resolves from the board's ``routing_policy`` FIRST — the single,
    operator-editable surface shown in the web settings "Routing" section —
    and falls back to the legacy board fields (``escalation_enabled``,
    ``failure_max_retries``) only when the policy is silent (Default First).

    This is what removes the "flag-gated reassignment path outside the
    policy engine": ``_maybe_escalate_model`` no longer reads
    ``board.escalation_enabled`` directly — it asks this engine, so a
    settings edit to ``routing_policy`` (no restart) governs the tier jump.
    """
    policy_cfg = getattr(board, "routing_policy", None) or {}
    if not isinstance(policy_cfg, Mapping):
        policy_cfg = {}

    # enabled: routing_policy wins when it names the key; else the legacy
    # board flag (which defaults True) — so an untouched board keeps its
    # existing behaviour.
    if "capability_escalation_enabled" in policy_cfg:
        enabled = bool(policy_cfg.get("capability_escalation_enabled"))
    else:
        enabled = bool(getattr(board, "escalation_enabled", True))

    escalate_after = capability_escalate_after(board)

    raw_max = policy_cfg.get("capability_max_escalations")
    try:
        max_escalations = int(raw_max)
        if max_escalations < 0:
            raise ValueError
    except (TypeError, ValueError):
        max_escalations = int(getattr(board, "failure_max_retries", 3) or 3)

    return CapabilityPolicy(
        enabled=enabled,
        escalate_after=escalate_after,
        max_escalations=max_escalations,
    )


def effective_policy_table(board=None) -> Dict[str, Dict[str, Any]]:
    """The full per-class policy as plain dicts, board overrides applied.

    Powers the read side of the web settings "Routing" section (task #328):
    the UI renders every failure class with its resolved action so the
    operator sees exactly what the system will do, not a hardcoded guess.
    """
    table: Dict[str, Dict[str, Any]] = {}
    for cls in DEFAULT_POLICY_TABLE:
        policy = resolve_policy({"failure_class": cls}, board=board)
        table[cls] = {
            "action": policy.action,
            "max_retries": policy.max_retries,
            "backoff_seconds": policy.backoff_seconds,
            "peer_fallback": policy.peer_fallback,
            "description": policy.description,
        }
    return table


# ── Dispatcher ──────────────────────────────────────────────────────


# Human-readable rule names for the three policy actions, so the
# history comment can name the routing rule instead of the bare action
# constant.  Matches the brief: "suggest exactly that, by rule name".
_RULE_NAMES: Dict[str, str] = {
    ACTION_AUTO_REQUEUE: "requeue same agent",
    ACTION_REASSIGN: "reassign to routing peer",
    ACTION_HUMAN: "hold for human",
}

# Brief-exact named resolutions: the history comment's "History suggests:"
# line uses one of the three canonical rule names the brief enumerates
# (task #336 — "requeue same agent / hold / infra retry").  The mapping
# below turns the fingerprint safe_action into the exact brief term:
#
#   requeue  → prior retry worked    → "requeue same agent"
#   hold     → mixed signal          → "hold"
#   escalate → retry never worked    → "hold"  (escalate falls through
#             to HUMAN in routing policy; the brief's exact term for
#             "don't auto-act" is "hold", not "hold for human").
_HISTORY_SUGGESTION: Dict[str, str] = {
    "requeue": "requeue same agent",
    "hold": "hold",
    "escalate": "hold",
}


def post_failure_history_comment(task) -> bool:
    """Post a dedicated history comment when the fingerprint has been
    seen before (task #336).

    First occurrence (no prior matches in the ledger) posts nothing —
    returns ``False``.  Second+ occurrence posts a comment linking the
    prior task and naming the routing policy's prescribed action —
    returns ``True``.

    The comment is informational only; it never changes the routing
    decision.  The policy dispatch in :func:`apply_failure_policy`
    handles the actual action; this function ensures the history is
    visible when a human or operator looks at the task.

    Comment shape (when prior was resolved)::

        Failure history: this fingerprint has been seen 1 time before.
        Fingerprint: 401 authentication_failed / claude / execution

        Prior task: #299 — Authentication error: 401 Unauthorized
        Prior outcome: resolved

        Routing policy (env_missing): hold for human — API key / auth /
        CLI missing — operator must fix environment.
        History suggests: hold

    The "History suggests:" line fires only when the most recent prior
    was resolved — that is, the brief's "If the prior occurrence ended
    in a known resolution" gate.  When the prior was failed, the
    "Prior outcome:" line still surfaces what happened (per the brief:
    "what fixed or was decided last time") and the routing policy line
    still names the policy action; the suggestion-by-exact-rule-name is
    only meaningful when a resolution exists to point at.
    """
    from .models import CommentType, TaskComment
    from .fingerprints import (
        STAGE_EXECUTION,
        _resolved_status,
        compute_fingerprint,
        matches_query,
        safe_action_for,
    )

    metadata = dict(task.metadata or {})
    failure_class = metadata.get("failure_class") or "unknown"

    fp = compute_fingerprint(
        failure_class=failure_class,
        reason=metadata.get("last_failure_reason", ""),
        agent=task.assignee.name if task.assignee_id else "",
        model=task.model_name or "",
        stage=STAGE_EXECUTION,
    )

    matches = [m for m in matches_query(fp) if m.task_id != task.id]
    if not matches:
        return False

    seen_count = len(matches)
    prior = matches[0]
    prior_status = _resolved_status(prior)
    prior_one_liner = prior.one_liner or "(no resolution recorded)"

    board = getattr(task, "board", None)
    policy = resolve_policy(metadata, board=board)
    rule_name = _RULE_NAMES.get(policy.action, policy.action)

    lines = [
        f"Failure history: this fingerprint has been seen {seen_count} "
        f"time{'s' if seen_count != 1 else ''} before.",
        f"Fingerprint: {fp}",
        "",
        f"Prior task: #{prior.task_id} — {prior_one_liner}",
        f"Prior outcome: {prior_status}",
        "",
        f"Routing policy ({failure_class}): {rule_name} — {policy.description}",
    ]

    if prior_status == "resolved":
        safe = safe_action_for(matches)
        suggestion = _HISTORY_SUGGESTION.get(safe, safe)
        lines.append(f"History suggests: {suggestion}")

    TaskComment.objects.create(
        task=task,
        schedule_run=task.current_schedule_run,
        author_email="odin+dag-executor@system",
        author_label="odin-dag-executor",
        content="\n".join(lines),
        comment_type=CommentType.STATUS_UPDATE,
    )
    return True


def apply_failure_policy(
    task,
    overrides: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> bool:
    """Apply the per-class policy to a FAILED task.

    Returns ``True`` if the task was auto-redispatched (FAILED →
    IN_PROGRESS via the legacy infra path), ``False`` otherwise
    (including HUMAN, REASSIGN, and "not FAILED" no-ops).

    This function is the policy dispatcher; it does NOT itself flip
    status — AUTO_REQUEUE delegates to the existing
    ``_maybe_auto_redispatch_infra_failure`` so the legacy counter,
    history, continuity, and execution-strategy trigger stay in one
    place.  REASSIGN marks the audit trail only; the actual reassign
    runs at reflection time in ``views._maybe_reassign_on_quota_failure``.
    HUMAN posts an audit comment and leaves the task FAILED.

    W6.3 (task #225): the fingerprint advice (see
    :mod:`tasks.fingerprints`) is consulted before the policy fires.
    Two overrides apply:

    * If the fingerprint history overwhelmingly says retry never works,
      the static ``AUTO_REQUEUE`` action is downgraded to ``HUMAN``
      for *this* occurrence, with the recent history quoted in the
      audit comment.
    * If the fingerprint history overwhelmingly says retry always
      works, the policy's ``backoff_seconds`` stamp is skipped — the
      next retry should not wait.

    The ``overrides`` argument is the resolved per-class settings
    dict (defaults to ``settings.DAG_EXECUTOR_FAILURE_POLICY_OVERRIDES``
    when None).  Operators tune retry bounds per host without code
    changes.
    """
    from .models import CommentType, TaskComment
    from .fingerprints import should_override_auto_requeue, should_skip_backoff

    if task.status != "FAILED":
        return False

    metadata = dict(task.metadata or {})
    if overrides is None:
        from django.conf import settings
        overrides = getattr(settings, "DAG_EXECUTOR_FAILURE_POLICY_OVERRIDES", None) or {}
    board = getattr(task, "board", None)
    policy = resolve_policy(metadata, overrides=overrides, board=board)
    failure_class = metadata.get("failure_class") or "unknown"

    # W6.3 (task #225): fingerprint advice can override the static
    # policy when the history is clear.  We compute the advice from
    # the SAME-task-filtered history so the count we use for the
    # override decision is independent of whether the caller's
    # `record_execution_mistake` ran before or after this dispatch.
    advice_text, matches = _fingerprint_advice_for_task(task, failure_class, metadata)

    # Task #336: post a dedicated history comment when the fingerprint
    # has been seen before.  First occurrence posts nothing; second+
    # links the prior task and names the routing rule.  Informational
    # only — never changes the routing decision below.
    post_failure_history_comment(task)

    if policy.action == ACTION_AUTO_REQUEUE:
        # Override to HUMAN when the history is overwhelmingly failed.
        # Brief: "one that says 'needed a human' escalates immediately
        # with the history quoted."
        if should_override_auto_requeue(
            _advice_from_matches(advice_text, matches)
        ):
            _post_history_escalation_audit(
                task, policy, failure_class, metadata, advice_text, matches,
            )
            return False
        # Positive-history path: skip the backoff (per brief: "can
        # requeue without waiting").  The override is sticky for this
        # call only — _auto_requeue reads backoff_seconds off the
        # policy and is told to zero it via the explicit
        # ``skip_backoff`` arg.
        skip_backoff = should_skip_backoff(
            _advice_from_matches(advice_text, matches)
        )
        # Was the same-agent cap ALREADY reached before this call? If so a
        # prior cycle already handled the exhaustion (and, if applicable,
        # the peer hop) — we must not re-fire, so repeated dispatches on a
        # capped task stay idempotent.
        cap_before = bool(
            metadata.get("policy_cap_reached")
            or metadata.get("auto_redispatch_cap_reached")
        )
        if _auto_requeue(
            task, policy, failure_class, metadata,
            advice_text=advice_text,
            skip_backoff=skip_backoff,
        ):
            return True
        # task #328: same-agent retries are exhausted (cap reached THIS
        # call). The policy for a protocol/infra class is "retry same
        # agent, THEN its routing peer, never a tier jump". Move to a
        # same-tier peer once before holding for a human.
        if policy.peer_fallback and not cap_before and _cap_reached(task):
            return _reassign_to_routing_peer(task, policy, failure_class)
        return False

    if policy.action == ACTION_REASSIGN:
        _post_reassign_audit(task, policy, failure_class, metadata, advice_text)
        return False

    # ACTION_HUMAN (or anything unrecognised — defensive).
    _post_human_audit(task, policy, failure_class, metadata, advice_text)
    return False


# ── Routing-peer fallback (task #328) ───────────────────────────────
# Metadata flag: set once when a protocol/infra failure has moved from
# the original assignee to a same-tier routing peer. Bounds the peer step
# to a single hop so a persistently-broken peer can't ping-pong forever.
PEER_REASSIGNED_KEY = "routing_peer_reassigned"


def _cap_reached(task) -> bool:
    """True if the same-agent auto-requeue cap was reached this cycle.

    Reads the flag ``_maybe_auto_redispatch_infra_failure`` stamps when
    the retry counter hits the policy cap. Distinguishes "retries
    exhausted → peer" from "someone else moved the task / no assignee".
    """
    task.refresh_from_db(fields=["metadata"])
    meta = task.metadata or {}
    return bool(meta.get("policy_cap_reached") or meta.get("auto_redispatch_cap_reached"))


def _reassign_to_routing_peer(task, policy: FailurePolicy, failure_class: str) -> bool:
    """Move a protocol/infra failure to a same-tier ROUTING PEER (task #328).

    The standing policy for a protocol/infra class is: retry the same
    agent (handled upstream via AUTO_REQUEUE), THEN its routing peer —
    a same-cost-tier alternative agent, chosen by the board's preference
    order — and only THEN hold for a human. This is deliberately NOT a
    tier jump: the peer sits in the same cost tier, so a protocol hiccup
    never silently escalates a cheap task onto expensive firepower.

    Bounded to a single hop (``PEER_REASSIGNED_KEY``). Posts a rule-named
    comment for every outcome so no switch is silent. Returns True when
    the task was requeued on a peer, False when it was left FAILED.
    """
    from django.db import transaction
    from .models import CommentType, Task, TaskComment, TaskHistory, TaskStatus
    from .kanban_ordering import move_task
    from .views import _find_alternative_agent, _agent_key

    task.refresh_from_db()
    if task.status != TaskStatus.FAILED:
        return False

    metadata = dict(task.metadata or {})
    if metadata.get(PEER_REASSIGNED_KEY):
        # Already hopped to a peer once — hold for a human rather than loop.
        return False

    old_assignee = task.assignee
    old_agent = old_assignee.name if old_assignee else "unassigned"
    old_model = task.model_name

    new_agent, new_model = _find_alternative_agent(task)
    if new_agent is None:
        TaskComment.objects.create(
            task=task,
            schedule_run=task.current_schedule_run,
            author_email="odin+dag-executor@system",
            author_label="odin-dag-executor",
            content=(
                f"Protocol/infra failure ({failure_class}): same-agent retries "
                f"exhausted and no same-tier routing peer is available. "
                f"Holding {old_agent} for human review — no tier jump."
            ),
            comment_type=CommentType.STATUS_UPDATE,
        )
        return False

    with transaction.atomic():
        locked = Task.objects.select_for_update().get(id=task.id)
        if locked.status != TaskStatus.FAILED:
            return False
        metadata = dict(locked.metadata or {})
        if metadata.get(PEER_REASSIGNED_KEY):
            return False
        metadata[PEER_REASSIGNED_KEY] = True
        metadata["routing_peer_from"] = old_agent
        metadata["routing_peer_at"] = timezone.now().isoformat()
        metadata["rework_count"] = int(metadata.get("rework_count", 0) or 0) + 1
        locked.assignee = new_agent
        if new_model:
            locked.model_name = new_model
        locked.metadata = metadata
        locked.status = TaskStatus.IN_PROGRESS
        locked.kanban_position = move_task(
            locked, target_status=TaskStatus.IN_PROGRESS, target_index=None,
        )
        locked.save(update_fields=[
            "assignee", "model_name", "status", "kanban_position",
            "metadata", "last_updated_at",
        ])
        TaskHistory.objects.create(
            task=locked, schedule_run=locked.current_schedule_run,
            field_name="assignee", old_value=old_agent,
            new_value=new_agent.name, changed_by="odin+dag-executor@system",
        )
        if new_model and new_model != old_model:
            TaskHistory.objects.create(
                task=locked, schedule_run=locked.current_schedule_run,
                field_name="model", old_value=old_model or "",
                new_value=new_model, changed_by="odin+dag-executor@system",
            )
        TaskHistory.objects.create(
            task=locked, schedule_run=locked.current_schedule_run,
            field_name="status", old_value=TaskStatus.FAILED,
            new_value=TaskStatus.IN_PROGRESS, changed_by="odin+dag-executor@system",
        )

    TaskComment.objects.create(
        task=locked, schedule_run=locked.current_schedule_run,
        author_email="odin+dag-executor@system",
        author_label="odin-dag-executor",
        content=(
            f"Protocol/infra failure ({failure_class}): same-agent retries "
            f"exhausted — routing to same-tier peer {old_agent} → "
            f"{new_agent.name}/{new_model or 'default'} (routing peer, NOT a "
            f"tier jump). Preference order picked {_agent_key(new_agent)}."
        ),
        comment_type=CommentType.STATUS_UPDATE,
    )

    try:
        from .execution import get_strategy
        strategy = get_strategy()
        if strategy:
            strategy.trigger(locked)
    except Exception:
        from logging import getLogger
        getLogger(__name__).exception(
            "[task:%s] execution strategy trigger failed after peer reassign",
            locked.id,
        )
    return True


# ── Internal helpers ───────────────────────────────────────────────


def _advice_from_matches(advice_text: str, matches) -> Dict[str, Any]:
    """Build the small advice dict ``should_override_auto_requeue`` /
    ``should_skip_backoff`` need from the matched MistakeEntry rows.

    The two predicates read two keys: ``safe_action`` (recomputed from
    the *current* matches) and ``seen_count`` (count after the
    SAME-task filter).  Both are derived from the matches directly so
    the override decision uses the same population as the audit
    comment's triage line.
    """
    from .fingerprints import safe_action_for

    rows = list(matches or [])
    return {
        "safe_action": safe_action_for(rows),
        "seen_count": len(rows),
    }


def _fingerprint_advice_for_task(task, failure_class: str, metadata: dict):
    """Compute the fingerprint advice for *task*, filtered to
    SAME-task-excluded prior matches.

    Returns ``(advice_text, matches)`` where ``advice_text`` is the
    formatted triage line ready to embed in an audit comment, and
    ``matches`` is the same-task-filtered list of prior
    MistakeEntry rows it was built from.

    The same-task filter is what makes the count stable regardless
    of whether ``record_execution_mistake`` already ran — the new
    entry is recorded either before (stale-recovery path) or after
    (views path) this call, and we want the math to match *prior*
    occurrences either way.
    """
    from .fingerprints import (
        advice_for_failure,
        format_advice_line,
        matches_query,
    )

    advice = advice_for_failure(
        failure_class=failure_class,
        reason=metadata.get("last_failure_reason", ""),
        agent=task.assignee.name if task.assignee_id else "",
        model=task.model_name or "",
        stage="execution",
    )
    matches = [
        m for m in matches_query(advice["fingerprint"]) if m.task_id != task.id
    ]

    # Build an advice text block that cites the SAME-task-filtered
    # matches (not the raw ``advice`` which can include the just-
    # recorded entry).  When we have matches, surface the most
    # recently resolved one's one-liner so the second occurrence
    # "quotes the first" per the brief.
    if matches:
        last_resolution = next(
            (
                getattr(m, "one_liner", "")
                for m in matches
                if _task_status_is_resolved(m)
            ),
            getattr(matches[0], "one_liner", "(no history)"),
        )
        from .fingerprints import safe_action_for as _safe
        rebuilt = {
            "fingerprint": advice["fingerprint"],
            "seen_count": len(matches),
            "safe_action": _safe(matches),
            "last_resolution": last_resolution,
        }
    else:
        rebuilt = {
            "fingerprint": advice["fingerprint"],
            "seen_count": 0,
            "safe_action": advice["safe_action"],
            "last_resolution": None,
        }

    advice_text = format_advice_line(rebuilt)
    return advice_text, matches


def _task_status_is_resolved(entry) -> bool:
    """True if the entry's underlying task is in a resolved terminal state.

    Used by ``_post_history_escalation_audit`` so the audit comment
    quotes a successful prior resolution when one exists.
    """
    from .fingerprints import _resolved_status
    return _resolved_status(entry) == "resolved"


def _auto_requeue(
    task, policy: FailurePolicy, failure_class: str, metadata: dict,
    *, advice_text: str = "", skip_backoff: bool = False,
) -> bool:
    """Delegate to the legacy infra auto-redispatch path.

    The legacy path handles:
      * counter increment (shared with legacy infra classes)
      * bounded retries + cap-reached flag
      * history entry
      * F45 continuity record
      * execution-strategy trigger

    The new layer adds the policy's ``backoff_seconds`` stamp on top.
    ``skip_backoff`` (W6.3 / task #225) zeros the backoff when the
    fingerprint history already proved the retry always works — the
    brief calls this out as "requeue without waiting".
    """
    # Stamp next_retry_after (used by downstream schedulers to honor
    # the backoff).  The legacy infra-redispatch path doesn't know
    # about backoff — we set the field BEFORE delegating so it lands
    # in the metadata write that follows.
    backoff = 0 if skip_backoff else policy.backoff_seconds
    if backoff > 0:
        metadata["next_retry_after"] = (
            timezone.now() + timedelta(seconds=backoff)
        ).isoformat()
        task.metadata = metadata
        task.save(update_fields=["metadata"])

    # Stamp the fingerprint advice onto the metadata so a future
    # replay can see what we did.  The dispatcher's audit comment
    # still quotes it inline (see ``_maybe_auto_redispatch_infra_failure``).
    if advice_text:
        metadata["last_fingerprint_advice"] = advice_text
        if skip_backoff:
            metadata["last_fingerprint_skip_backoff"] = True
        task.metadata = metadata
        task.save(update_fields=["metadata"])

    # Delegate to the policy-aware infra auto-redispatch.  Passing
    # the resolved policy enables auto-retry for ALL auto_requeue
    # classes (transport_error, truncation, silent_hang, lock_race)
    # and uses the policy's max_retries + description in the audit
    # trail.  The legacy hook re-checks task.status == FAILED inside
    # its own lock; if a concurrent worker beat us to it the call is
    # a no-op.  ``advice_text`` (W6.3) is forwarded so the auto-requeue
    # audit comment also surfaces the fingerprint history line.
    from .dag_executor import _maybe_auto_redispatch_infra_failure
    return _maybe_auto_redispatch_infra_failure(
        task, policy=policy, advice_text=advice_text or "",
    )


def _post_reassign_audit(
    task, policy: FailurePolicy, failure_class: str, metadata: dict,
    advice_text: str = "",
) -> None:
    """Post an audit comment naming the reassign intent.

    The actual reassign lives in ``views._maybe_reassign_on_quota_failure``
    at reflection time (it has the harness_usage_status ground-truth
    check that decides EXHAUSTED vs HEADROOM).  At the FAILED
    transition we only stamp the policy intent + an audit comment.
    The optional ``advice_text`` (W6.3) appends the fingerprint
    triage line so an operator scanning the comment knows what
    history the dispatcher saw.
    """
    from .models import CommentType, TaskComment

    task.refresh_from_db()
    metadata = dict(task.metadata or {})
    if "policy_class" not in metadata:
        metadata["policy_class"] = failure_class
        metadata["policy_action"] = policy.action
        metadata["policy_at"] = timezone.now().isoformat()
        if advice_text:
            metadata["last_fingerprint_advice"] = advice_text
        body = (
            f"Failure policy: class={failure_class} action={policy.action}. "
            f"{policy.description}\n"
            f"Next action: reassign on reflection (or backoff if "
            f"provider has headroom) — the actual reassign runs in "
            f"views._maybe_reassign_on_quota_failure with the "
            f"harness_usage_status ground-truth check."
        )
        if advice_text:
            body += "\n\n" + advice_text
        TaskComment.objects.create(
            task=task,
            schedule_run=task.current_schedule_run,
            author_email="odin+dag-executor@system",
            author_label="odin-dag-executor",
            content=body,
            comment_type=CommentType.STATUS_UPDATE,
        )
        task.metadata = metadata
        task.save(update_fields=["metadata"])


def _post_human_audit(
    task, policy: FailurePolicy, failure_class: str, metadata: dict,
    advice_text: str = "",
) -> None:
    """Post an audit comment naming the class + action for the operator.

    Without this comment a stuck FAILED task is indistinguishable from
    a forgotten one.  The comment must include the failure class, the
    action taken (human), and the description (so the operator knows
    WHY nothing auto-fired).  When ``advice_text`` is supplied (W6.3)
    the fingerprint triage line is appended so the operator also
    sees what history looked like for this shape.
    """
    from .models import CommentType, TaskComment

    body = (
        f"Failure policy: class={failure_class} action={policy.action}. "
        f"{policy.description}\n"
        f"Next action: surface for human review — no automatic retry."
    )
    if advice_text:
        body += "\n\n" + advice_text
    TaskComment.objects.create(
        task=task,
        schedule_run=task.current_schedule_run,
        author_email="odin+dag-executor@system",
        author_label="odin-dag-executor",
        content=body,
        comment_type=CommentType.STATUS_UPDATE,
    )


def _post_history_escalation_audit(
    task, policy: FailurePolicy, failure_class: str, metadata: dict,
    advice_text: str, matches,
) -> None:
    """Post an audit comment when fingerprint history downgrades
    ``AUTO_REQUEUE`` to ``HUMAN`` (W6.3).

    The brief mandates that we *quote* the history in the escalation
    comment so an operator can see WHY the retry path was skipped —
    not just a generic class+action line.  We append the most recent
    prior attempt(s) as a small table, one line per occurrence.
    """
    from .models import CommentType, TaskComment
    from .fingerprints import history_lines

    history_block = "\n".join(history_lines(matches, limit=5)) or "  (no history records)"

    body = (
        f"Failure policy: class={failure_class} action={policy.action} "
        f"(overridden to human by fingerprint history).\n"
        f"{policy.description}\n"
        f"Next action: surface for human review — auto-retry skipped "
        f"because prior occurrences of this fingerprint never self-healed.\n\n"
        f"{advice_text}\n"
        f"Recent fingerprint history:\n{history_block}"
    )
    TaskComment.objects.create(
        task=task,
        schedule_run=task.current_schedule_run,
        author_email="odin+dag-executor@system",
        author_label="odin-dag-executor",
        content=body,
        comment_type=CommentType.STATUS_UPDATE,
    )
    # Stamp the override on metadata so a later reflection / dashboard
    # view can see why nothing auto-fired.
    metadata = dict(task.metadata or {})
    metadata["fingerprint_override_at"] = timezone.now().isoformat()
    metadata["fingerprint_override_class"] = failure_class
    metadata["fingerprint_override_seen_count"] = len(matches)
    metadata["fingerprint_override_safe_action"] = "escalate"
    task.metadata = metadata
    task.save(update_fields=["metadata"])