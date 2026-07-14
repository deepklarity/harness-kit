# Quota Failover — Detailed Trace

All line numbers verified against the spec branch (W3.11 merge `0d60be7e`). Backend paths are under `taskit/taskit-backend/`.

## 1. Detection — raw error tagged at execution time

**File**: `odin/src/odin/orchestrator.py`
**Function**: `Orchestrator._classify_failure()` — line ~3291
An execution exception whose text matches `http/api/rate/quota/token/429` is classified `failure_type="llm_call_failure"`. The returned dict (`failure_type`, `failure_reason`, `failure_origin`, `failure_phase`) is persisted to `task.metadata` as `last_failure_type` / `last_failure_reason`.

**File**: `tasks/failure_tagger.py`
**Symbol**: `QUOTA_KEYWORDS` — line ~38
Canonical keyword list (`"quota"`, `"rate limit"`, `"rate_limit"`, `"429"`, `"too many requests"`, …). The backend imports this so detection stays in lockstep with the tagger — there is no second inline keyword list anymore.

## 2. The quota-failure gate

**File**: `tasks/views.py`
**Function**: `_is_quota_failure(task, report)` — line ~358
Imports `QUOTA_KEYWORDS` (aliased `_QUOTA_KEYWORDS`) at line ~355. Checks three sources, in order:
1. The reflection report's `quota_failure` field (requires an **affirmative** keyword match — a "None detected" string no longer coerces to True; this is the F45 fix).
2. `task.metadata["last_failure_type"] == "llm_call_failure"` **and** a keyword in the reason.
3. `verdict_summary` keyword match.

The `quota_failure` field is populated by the reviewer: `odin/src/odin/reflection.py` parses a "quota / resource failure" section and stores it on `ReflectionReport.quota_failure` (`tasks/models.py` ~line 445, a `TextField`).

## 3. Ground-truth fetch (the network boundary)

**File**: `tasks/views.py`
**Function**: `_get_usage_from_provider(provider)` — line ~443
In-process import, **not** a subprocess:
```python
from harness_usage_status.config import load_config
from harness_usage_status.providers.registry import get_provider
```
Awaits `provider.get_usage()` via `_run_usage_coro` (line ~422, bridges async→sync Django) and returns `usage.compute_pct()`. Missing package → `ImportError` → `(None, "harness_usage_status package not installed")`.

**Function**: `_check_provider_usage(agent_key)` — line ~477
Maps agent identity → `harness_usage_status` provider name via `_AGENT_TO_USAGE_PROVIDER` (line ~406, mirrors odin's `QUOTA_PROVIDER_MAP`), fetches usage, classifies against `_QUOTA_EXHAUSTED_PCT = 95.0` (line ~402), and returns a `QuotaGroundTruth` namedtuple `(state, usage_pct, detail)`. Never raises. States (line ~415): `_QUOTA_EXHAUSTED`, `_QUOTA_HEADROOM`, `_QUOTA_UNAVAILABLE`.

## 4. Decide — backoff vs reassign

**File**: `tasks/views.py`
**Function**: `_maybe_reassign_on_quota_failure(task, report)` — line ~616 (branch point ~636–690)
- ~636: not a quota failure → `return False` (no-op; caller does normal rework).
- ~645: `verification = _check_provider_usage(agent_key)`.
- ~649: `state == _QUOTA_HEADROOM` → `_record_rate_limit_backoff()`, post a "Transient rate-limit … provider has headroom … keeping assignee and requeuing with backoff" STATUS_UPDATE, `return True` **without touching assignee/model**.
- ~668: `verified = state == _QUOTA_EXHAUSTED`; `_QUOTA_UNAVAILABLE` falls through to reassign but sets an `unverified_note` (~669) appended to the comment.

**Function**: `_record_rate_limit_backoff(task, agent, usage_pct, detail)` — line ~737
Writes `task.metadata["rate_limit_backoff"] = {at, agent, usage_pct, detail}` so repeated transient 429s are visible. (Visibility signal — there is no literal sleep yet.)

## 5. Reassign — switch to fallback

**File**: `tasks/views.py`
**Function**: `_maybe_reassign_on_quota_failure` — reassign block line ~674–734
- ~674: `new_agent, new_model = _find_alternative_agent(task)`; if none → post "no alternative agent available", `return True`.
- ~692: set `task.assignee = new_agent`, `task.model_name = new_model`, save.
- ~699: write TaskHistory rows for both `assignee` and `model_name` changes.
- ~719: post "Reassigned to X …" comment (prefixed with `unverified_note` when the checker was unavailable).
- ~730: log `Quota failure reassignment (verified=%s): X/Y → Z/W`.

**Function**: `_find_alternative_agent(task)` — line ~507
- ~535: board members `role=AGENT`, excluding current assignee.
- ~529: filtered to `get_active_agents()` (active lineup from `agent_models.json`) — the F45 fix that stops dispatch to retired providers.
- ~555: prefers the failed agent's `cost_tier`; falls back to any active AGENT.
- ~570: model from `_default_model_for_user`.

## 6. Loop call site

**File**: `tasks/views.py`
**Function**: `ReflectionReportViewSet.partial_update()` — line ~3566, quota hop at line ~3676
Inside the NEEDS_WORK/FAIL, `completed_count < 3` branch:
```python
quota_handled = _maybe_reassign_on_quota_failure(task, report)     # ~3676
_record_rework_continuity(..., suppress_comment=quota_handled)     # ~3683
# REVIEW → IN_PROGRESS, re-trigger execution strategy               # ~3690-3699
```
Pre-rework assignee/model are snapshotted at ~3665 for the continuity audit. The re-trigger is gated on `DAG_EXECUTOR_MAX_CONCURRENCY` — at capacity the task stays queued via `_set_dispatch_blocked_reason(task, "concurrency_cap_reached")`.

## 7. The ground-truth source: `harness_usage_status`

**Package**: `harness_usage_status/` (source under `harness_usage_status/src/harness_usage_status/`).
- **CLI entrypoint**: `pyproject.toml` → `harness-usage-status = "harness_usage_status.cli:main"`; `cli.py :: main` (~line 222) → `fire.Fire(HarnessUsageStatus)`.
- **Ground-truth command** (equivalent to what the backend imports): `harness-usage-status quota --provider <name> --output json` → `cli.py :: HarnessUsageStatus.quota` (~line 37).
- **The number that matters**: `models.py :: UsageInfo.compute_pct` (~line 27) = `round(used / quota_limit * 100, 1)`, compared to `_QUOTA_EXHAUSTED_PCT = 95.0`.
- **Providers**: `providers/{claude_code,codex,gemini,minimax,glm}.py` via `providers/registry.py`; CLI-backed providers use `cli_runner.py`, API-backed (minimax, glm) hit HTTP.

The backend does **not** shell out — it imports `config.load_config`, `providers.registry.get_provider`, `provider.get_usage()` directly (same data path, in-process). Odin's router does the same in `orchestrator.py :: _fetch_quota` (~line 2461).

## Cross-references
- Reflection loop that invokes this: `../spec-task-lifecycle/03-reflection-loop/` and `../task-state-machine-celery-automation/DETAILS.md` §9 (reassignment inside the state machine).
- Reflection verdict → rework dispatch (where §6 sits): `../spec-task-lifecycle/03-reflection-loop/DETAILS.md`.
