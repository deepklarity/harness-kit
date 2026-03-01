# Trace Data Pipeline -- Debug Guide

## Log locations

| Layer | Log file | What's in it |
|-------|----------|-------------|
| Trace (raw) | `.odin/logs/task_{task_id}.trace.jsonl` | Raw stream-json from harness subprocess (every protocol event) |
| Output (text) | `.odin/logs/task_{task_id}.out` | Extracted plain text only |
| Odin run | `.odin/logs/run_{run_id}.jsonl` | Structured orchestrator events (task start/complete, errors) |
| Django | `taskit/taskit-backend/logs/taskit.log` | Request/response, view errors, execution result processing |
| Frontend | Browser console | API errors, TraceViewer parse failures |

## What to search for

| Symptom | Where to look | Search term / action |
|---------|--------------|---------------------|
| Token count shows 0 or "---" | Backend comment for task | Check if comment contains token counts inline; check `compute_usage_from_trace()` |
| Cost shows $0.00 for non-Claude harness | `pricing.py` | Non-Claude harnesses may not emit tokens; check if model has pricing entry |
| TraceViewer shows "unknown format" | Raw trace comment content | First line should be valid JSON; check `detectTraceFormat()` conditions |
| TraceViewer timeline is empty | `TraceViewer.tsx` | Format detected but no matching events; check `buildClaudeCodeTimeline()` or `buildOdinTimeline()` |
| Token summary missing cache stats | Trace JSONL | Claude `modelUsage` event must include `cacheReadInputTokens`; `step_finish` fallback also has `cache.read`/`cache.write` |
| Stale execution result ignored | `views.py` execution_result | Check `ignore_execution_results` or `stopped_run_token` in task metadata |
| Cost not accumulating across retries | `views.py` line ~1313 | `total_estimated_cost_usd += exec_meta["estimated_cost_usd"]` -- verify both sides are numeric |
| Trace file empty / missing | `.odin/logs/` | Harness subprocess may have crashed before writing; check process exit code |
| Codex trace looks like plain text | Expected | Codex has no `--output-format` flag; trace file contains raw text, not JSONL |
| input + output tokens < total tokens | `summary.json` in snapshot | Expected for minimax/glm -- they include reasoning tokens in total that aren't broken out |
| Trace comment truncated | `orchestrator.py` `_truncate_trace()` | Large traces are truncated to preserve tail (last 2000 chars with `modelUsage`) |
| `last_usage` field is null/missing | Expected | Deprecated field; usage now computed on-the-fly from comments via `compute_usage_from_trace()` |

## Quick commands

```bash
# Check token usage for a specific task (computed from comments)
cd taskit/taskit-backend && python testing_tools/task_inspect.py <task_id> --json --sections basic,tokens

# Check aggregate cost and token data for a spec
cd taskit/taskit-backend && python testing_tools/spec_trace.py <spec_id> --sections tasks

# View raw trace file for a task (if still on disk)
cat .odin/logs/task_<task_id>.trace.jsonl | python -m json.tool --no-ensure-ascii 2>/dev/null || cat .odin/logs/task_<task_id>.trace.jsonl

# Check what harness output format flags are used
cd odin && python -c "
from odin.harnesses.registry import get_harness, _import_all_harnesses
from odin.models import OdinConfig, AgentConfig
_import_all_harnesses()
for name in ['claude','gemini','qwen','minimax','glm','codex']:
    try:
        h = get_harness(name, AgentConfig(cli_command=name, enabled=True))
        cmd = h.build_execute_command('test', {})
        print(f'{name}: {\" \".join(cmd)}')
    except: print(f'{name}: (error)')
"

# Validate trace JSONL structure (first 5 lines)
head -5 .odin/logs/task_<task_id>.trace.jsonl | while read line; do echo "$line" | python -m json.tool > /dev/null 2>&1 && echo "OK: ${line:0:80}" || echo "INVALID: ${line:0:80}"; done

# Check if a task has trace comments in the DB
cd taskit/taskit-backend && python -c "
import django; import os; os.environ['DJANGO_SETTINGS_MODULE']='config.settings'; django.setup()
from tasks.models import TaskComment
for c in TaskComment.objects.filter(task_id=<TASK_ID>):
    atts = c.attachments or []
    is_trace = 'trace:execution_jsonl' in atts
    print(f'Comment {c.id}: trace={is_trace}, len={len(c.content)}, attachments={atts}')
"

# Run the trace logging unit tests
cd odin && python -m pytest tests/mock/test_trace_logging.py -v

# Run the e2e snapshot regression tests
python -m pytest tests/e2e_snapshots/ -v

# Capture a fresh snapshot from a completed spec
cd taskit/taskit-backend && python testing_tools/snapshot_extractor.py <spec_id> ../../tests/e2e_snapshots/<name>

# Capture slim snapshot (no large text fields, smaller diff)
cd taskit/taskit-backend && python testing_tools/snapshot_extractor.py <spec_id> ../../tests/e2e_snapshots/<name> --slim
```

## Env vars that affect this flow

| Variable | Effect | Default |
|----------|--------|---------|
| `ODIN_EXECUTION_STRATEGY` | Determines execution path (local subprocess vs celery) | `local` |
| `MINIMAX_API_KEY` | Enables minimax harness when set | (unset) |
| `TASKIT_BACKEND_URL` | Where orchestrator posts execution results | `http://localhost:8000` |

## Common breakpoints

- `odin/src/odin/harnesses/base.py:read_with_trace()` -- see raw lines from subprocess, verify dual-write
- `odin/src/odin/harnesses/claude.py:_extract_token_usage()` -- verify token parsing from `modelUsage` or `step_finish`
- `odin/src/odin/orchestrator.py:_execute_task()` ~line 2367 -- where trace is read from disk for backend submission
- `odin/src/odin/orchestrator.py:_truncate_trace()` -- verify truncation preserves tail with usage events
- `taskit/taskit-backend/tasks/views.py:execution_result()` -- where raw_output is processed and stored
- `taskit/taskit-backend/tasks/serializers.py:get_usage()` -- where on-the-fly usage computation happens
- `taskit/taskit-frontend/src/components/TraceViewer.tsx:parseTrace()` -- where raw JSONL is split and parsed
- `taskit/taskit-frontend/src/components/TraceViewer.tsx:detectTraceFormat()` -- where format detection can fail

## Field mapping cheat sheet

When debugging a mismatch between what the backend stores and what the frontend shows:

| What you see in UI | Frontend source | API field | Backend source |
|----|----|----|---|
| Token count on task card | `task.usage.total_tokens` | `usage` (SerializerMethodField) | `compute_usage_from_trace(task)` -- parses comments |
| Cost on task card | `task.estimatedCostUsd` | `estimated_cost_usd` (SerializerMethodField) | `compute_task_estimated_cost(task)` -- model pricing * usage |
| Duration on task card | `task.metadata.last_duration_ms` | `metadata.last_duration_ms` | Set in `execution_result()` view |
| Token summary in TraceViewer | `extractTokenSummary(events)` | N/A (parsed client-side) | Raw JSONL in trace comment content |
| Cost summary on spec | `spec.costSummary` | `cost_summary` (SerializerMethodField) | `compute_spec_cost_summary(tasks)` |
| Model name | `task.modelName` | `model_name` | DB field `Task.model_name` |

## Snapshot golden data reference

The `full_harness_smoke` snapshot (`tests/e2e_snapshots/full_harness_smoke/`) is the canonical regression baseline:

| File | Size | Key fields for trace debugging |
|------|------|-------------------------------|
| `tasks.json` | 23 KB | `metadata.last_usage`, `metadata.last_duration_ms`, `metadata.full_output`, `model_name` |
| `comments.json` | 170 KB | 42 comments; trace comments have `attachments: ["trace:execution_jsonl"]` |
| `summary.json` | 568 B | `total_tokens: 547337`, `total_input_tokens: 354911`, `total_output_tokens: 11753` |

Harnesses exercised: qwen, gemini, claude, minimax, codex, glm (all 6 production harnesses).

Known data quirk: `input_tokens + output_tokens (366,664) < total_tokens (547,337)` because minimax and glm include reasoning/internal tokens in total that aren't broken out into input/output.

## Regression testing coverage

| What's tested | Where | Confidence |
|---------------|-------|-----------|
| Harness output format flags | `odin/tests/mock/test_trace_logging.py` | High |
| Per-line JSON extraction (all formats) | `odin/tests/mock/test_trace_logging.py` | High |
| Dual-file write (trace + output) | `odin/tests/mock/test_trace_logging.py` | High |
| Task shape with usage/duration/cost | `tests/e2e_snapshots/` snapshot tests | High |
| All harnesses produce ODIN-STATUS SUCCESS | `tests/e2e_snapshots/` snapshot tests | High |
| Frontend cost formatting | `costEstimation.test.ts` | Medium (formatter only) |
| Frontend metrics parsing from comments | `diagnostics.test.ts` | Medium |
| Raw JSONL structure validity | Not tested | Gap |
| Trace comment existence per task | Not tested | Gap |
| Reflection trace capture | Not tested | Gap |
| TraceViewer format detection | Not tested | Gap |
