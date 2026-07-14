# Quota Failover + Provider Reassignment

Trigger: a task's LLM call fails with a quota / rate-limit error (429, "quota", "too many requests"), surfaced through the reflection loop.
End state: either the **same** agent is requeued with backoff (provider still has headroom), or the task is **reassigned** to a same-cost-tier fallback agent (provider genuinely exhausted), or the task FAILS after 3 reflection attempts.

Merged on the spec branch as **W3.11** (`fix(quota-failover): verify ground truth via harness_usage_status before switching providers; 429-with-headroom -> backoff same agent`).

## Where this lives

The detection→verify→decide logic lives **entirely in the TaskIt backend** (`tasks/views.py`) and runs **in the reflection loop** (post-execution), NOT during live `odin exec`. Odin's only roles are (1) tagging the raw error `llm_call_failure` at execution time and (2) shipping the `harness_usage_status` package that `views.py` imports for ground truth.

```
odin exec (task fails on 429)
  → orchestrator._classify_failure() tags failure_type="llm_call_failure"
  → task.metadata["last_failure_type"/"last_failure_reason"] persisted
  → task → REVIEW, auto-reflection fires
       │
       ▼
reflection reviewer runs, PATCHes report
  → ReflectionReportViewSet.partial_update() (views.py)
       verdict NEEDS_WORK/FAIL, completed_count < 3
       │
       ▼
  _maybe_reassign_on_quota_failure(task, report)
       │
       ├─ _is_quota_failure(task, report)?  ── no ──▶ return False (normal rework, same agent)
       │        yes
       ▼
  _check_provider_usage(agent)  ── ground truth via harness_usage_status ──┐
       │                                                                   │
       ├─ HEADROOM (usage < 95%)  ──▶ _record_rate_limit_backoff()         │
       │                              keep assignee, requeue same agent    │
       │                              (429 was transient)                  │
       │                                                                   │
       ├─ EXHAUSTED (usage ≥ 95%)  ─┐                                       │
       ├─ UNAVAILABLE (checker      │─▶ _find_alternative_agent()          │
       │   missing/errored)         │    reassign to same-cost-tier agent  │
       │                            │    (UNAVAILABLE reassigns but tags    │
       │                            │     the comment "unverified")         │
       ▼                            ▼                                       │
  task.assignee / model_name updated, TaskHistory rows written  ◀──────────┘
       │
       ▼
  status REVIEW → IN_PROGRESS, execution strategy re-triggered
  (gated on DAG_EXECUTOR_MAX_CONCURRENCY — stays queued if at capacity)
```

## The ground-truth decision (the W3.11 core)

Before W3.11, a quota-**keyword** match reassigned unconditionally. The bug: a single transient 429 (burst, not exhaustion) would bounce the task to a different provider needlessly, and a provider that was fine got abandoned. W3.11 adds a **ground-truth check** against real usage numbers:

| Ground-truth state | Condition | Action |
|--------------------|-----------|--------|
| `HEADROOM` | live usage `< 95%` | **Backoff same agent.** The 429 was transient; requeue the current assignee, record `rate_limit_backoff` in metadata. |
| `EXHAUSTED` | live usage `≥ 95%` | **Reassign.** Provider is genuinely out; switch to a fallback agent of the same cost tier. |
| `UNAVAILABLE` | `harness_usage_status` missing/errored | **Reassign, flagged unverified.** Falls back to the pre-W3.11 keyword behavior but the comment says the switch is unverified. |

The threshold is `_QUOTA_EXHAUSTED_PCT = 95.0` (`views.py`). Usage percent comes from `harness_usage_status` (`UsageInfo.compute_pct = used / quota_limit * 100`), imported **in-process** (not shelled out).

## Fallback-agent selection

`_find_alternative_agent(task)` picks the replacement:
1. Board members with `role=AGENT`, excluding the current assignee.
2. Filtered to the **active lineup** in `agent_models.json` via `get_active_agents()` — retired providers never get dispatched.
3. **Prefers the same `cost_tier`** as the failed agent, so a quota switch doesn't silently up/down-grade cost.
4. Falls back to any active AGENT user if no same-tier match; model from `_default_model_for_user`.
5. Returns `(None, None)` → task posts "no alternative agent available" and continues as normal rework.

## Failure modes

| Scenario | Behavior |
|----------|----------|
| Transient 429, provider healthy | HEADROOM → same agent requeued, `rate_limit_backoff` stamped, no reassign |
| Provider truly exhausted | EXHAUSTED → reassigned to same-tier fallback, TaskHistory records the switch |
| `harness_usage_status` not installed | UNAVAILABLE → reassigns on keyword alone, comment flagged "unverified" |
| No same-tier fallback available | Reassigns to any active agent; if none, stays on same agent |
| Non-quota reflection failure | `_is_quota_failure` returns False → normal rework path, no provider logic runs |
| 3rd reflection attempt | REVIEW → FAILED before any reassign (attempt cap wins) |
| At concurrency cap on requeue | Task stays queued (`dispatch_blocked_reason="concurrency_cap_reached"`), retried when a slot frees |
