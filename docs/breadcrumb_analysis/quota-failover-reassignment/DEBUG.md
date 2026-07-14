# Quota Failover — Debug Guide

## Log Locations

| Layer | Log file | What's in it |
|-------|----------|-------------|
| Detection (odin) | `.odin/logs/run_<run_id>.jsonl` | The raw 429/quota exception, `failure_type=llm_call_failure` classification |
| Reflection loop | `taskit/taskit-backend/logs/taskit.log` | `Quota failure reassignment (verified=…)`, backoff/reassign decisions |
| Tracebacks | `taskit/taskit-backend/logs/taskit_detail.log` | `harness_usage_status` import errors, provider fetch failures |
| Ground truth | run `harness-usage-status quota --provider <name> --output json` | Live usage_pct — the same number `_check_provider_usage` reads |

## What to Search For

| Symptom | Where to look | Search term / command |
|---------|--------------|----------------------|
| Task retrying the **same** agent after a 429 | Task comments / metadata | This is often **correct** now — look for "provider has headroom" comment or `metadata["rate_limit_backoff"]`. Not a detection miss. |
| Task reassigned to a different provider | Task history / comments | grep `taskit.log` for `Quota failure reassignment` — `verified=True` means ground-truth confirmed exhaustion |
| Reassign happened but you expected backoff | `taskit.log` | Check the logged `usage_pct` — if it read `≥ 95%` the switch was correct; if the checker was `UNAVAILABLE` the comment is flagged "unverified" |
| "unverified" in the reassign comment | `taskit_detail.log` | `harness_usage_status package not installed` or a provider fetch error → checker returned `_QUOTA_UNAVAILABLE` |
| Reassigned to wrong-cost-tier agent | `views.py :: _find_alternative_agent` | Verify the failed agent's `cost_tier`; check `get_active_agents()` output |
| Reassigned to a retired provider | `agent_models.json` | Confirm the target is in the active lineup — `get_active_agents()` should exclude retired ones |
| Quota failure not detected at all | Task metadata / report | `_is_quota_failure` needs `last_failure_type=llm_call_failure`+keyword, or an affirmative `report.quota_failure`, or a `verdict_summary` keyword |
| Stuck queued after reassign | Task metadata | `dispatch_blocked_reason="concurrency_cap_reached"` — at `DAG_EXECUTOR_MAX_CONCURRENCY`, retried when a slot frees |

## Quick Commands

```bash
# Ground truth for a provider (what the backend reads in-process)
harness-usage-status quota --provider claude_code --output json
harness-usage-status status                       # all providers at a glance

# Was this a quota failure? Inspect the task + its reflection
cd taskit/taskit-backend
python testing_tools/task_inspect.py <task_id> --json --sections basic
python testing_tools/reflection_inspect.py <report_id> --sections verdict,diagnosis

# Check the failure classification + backoff metadata
python -c "
from tasks.models import Task
t = Task.objects.get(id=<task_id>)
print('last_failure_type:', t.metadata.get('last_failure_type'))
print('last_failure_reason:', (t.metadata.get('last_failure_reason') or '')[:200])
print('rate_limit_backoff:', t.metadata.get('rate_limit_backoff'))"

# Follow the decision live
tail -f logs/taskit.log | grep -Ei 'quota|rate.limit|reassign|headroom'
```

## Config That Affects This Flow

| Symbol / key | Effect | Value |
|--------------|--------|-------|
| `_QUOTA_EXHAUSTED_PCT` (`views.py`) | Usage % at/above which a provider is "exhausted" → reassign | `95.0` |
| `QUOTA_KEYWORDS` (`failure_tagger.py`) | Strings that mark an error as a quota failure | `quota`, `rate limit`, `429`, … |
| `_AGENT_TO_USAGE_PROVIDER` (`views.py`) | Maps agent identity → `harness_usage_status` provider | mirrors odin's `QUOTA_PROVIDER_MAP` |
| `agent_models.json` (`get_active_agents`) | The active lineup fallbacks are drawn from | retired providers excluded |
| `DAG_EXECUTOR_MAX_CONCURRENCY` | Caps concurrent requeues after reassign | default `10` |

## Common Breakpoints

- `views.py :: _is_quota_failure()` — confirm which of the 3 sources tripped (report field vs metadata vs verdict summary)
- `views.py :: _check_provider_usage()` — verify the mapped provider name and the returned `(state, usage_pct)`
- `views.py :: _get_usage_from_provider()` — the import boundary; catch `ImportError` / provider errors here
- `views.py :: _maybe_reassign_on_quota_failure()` — the backoff-vs-reassign branch (~636–690)
- `views.py :: _find_alternative_agent()` — verify cost-tier preference and active-lineup filter

## Gotchas

- **Same-agent retry after a 429 is a feature, not a bug.** Pre-W3.11 docs treated a missing `Quota failure reassignment` log line as "detection failed." Now, HEADROOM deliberately keeps the same agent — check for the backoff comment before assuming the detector broke.
- **The backend imports `harness_usage_status`, it does not shell out.** If usage always reads `UNAVAILABLE`, the package isn't importable from the Django process — check the venv, not the CLI.
- **`compute_pct` needs a `quota_limit`.** A provider that doesn't expose a limit yields no percent → `UNAVAILABLE` → keyword-only reassign.
