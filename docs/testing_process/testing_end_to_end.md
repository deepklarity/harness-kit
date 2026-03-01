# End-to-End Testing

Three testing layers, each catching different bug classes:

| Layer | Location | Catches | Speed |
|-------|----------|---------|-------|
| **Unit/mock** | `odin/tests/`, `taskit/taskit-backend/tests/` | Logic bugs, API contracts, model validation | < 5s |
| **Snapshot** | `tests/e2e_snapshots/` | Field drift, lifecycle violations, pipeline breakage | < 1s (no DB) |
| **Live e2e** | `odin/temp_test_dir/` | Real harness failures, auth issues, race conditions | Minutes |

Snapshot tests bridge unit and live: they validate real execution data without live services.

## Diagnostic Scripts

All scripts live in `taskit/taskit-backend/testing_tools/` and use Django ORM directly (no auth needed). Run from `taskit/taskit-backend/`.

### Quick reference

| Script | Input | Purpose |
|--------|-------|---------|
| `task_inspect.py <task_id>` | Task PK | Deep single-task: metadata, comments, history, deps, problems |
| `spec_trace.py <spec_id>` | Spec PK | Full spec trace: tasks, deps, timeline, problems |
| `board_overview.py [board_id]` | Board PK (optional) | Board-level summary of all specs and tasks |
| `reflection_inspect.py <report_id>` | Report PK | Reflection verdict, sections, token usage, problems |
| `snapshot_extractor.py <spec_id> [dir]` | Spec PK | Export spec as JSON for snapshot testing |

### Output modes

Every script (except `snapshot_extractor`) supports output modes to control verbosity. This matters when LLMs consume the output — brief mode can be 5-8x cheaper in tokens.

| Flag | Effect |
|------|--------|
| `--brief` | 1-3 lines: status, key metrics, problem count |
| *(default)* | All sections, truncated content (backward compatible) |
| `--full` | Everything: full comments, full descriptions, full metadata |
| `--json` | Structured JSON — most token-efficient for LLM consumption |

Section filtering (task_inspect, spec_trace, reflection_inspect):

```bash
python testing_tools/task_inspect.py 42 --json --sections basic,tokens
python testing_tools/spec_trace.py 15 --sections tasks,problems
python testing_tools/reflection_inspect.py 8 --brief
```

Available sections per script:

- **task_inspect**: `basic`, `deps`, `metadata`, `description`, `history`, `comments`, `diagnosis`
- **spec_trace**: `header`, `tasks`, `deps`, `timeline`, `comments`, `problems`
- **reflection_inspect**: `basic`, `verdict`, `sections`, `tokens`, `prompt`, `task_context`, `diagnosis`

### snapshot_extractor

Exports a spec run as JSON files for regression testing.

```bash
python testing_tools/snapshot_extractor.py <spec_id> <output_dir>
python testing_tools/snapshot_extractor.py <spec_id> <output_dir> --slim
```

`--slim` excludes large text fields (descriptions, full_output, comment bodies) — useful when testing structural invariants only.

Output files:

| File | Contents |
|------|----------|
| `snapshot.json` | Complete dump |
| `spec.json` | Spec metadata |
| `tasks.json` | All tasks with metadata |
| `comments.json` | All comments |
| `history.json` | All field mutations |
| `summary.json` | Aggregate stats |

## Snapshot Testing

### What the tests validate

Tests assert **structural invariants**, not exact values:

- **Spec shape**: Required fields present, not abandoned, has content
- **Task shape**: Required fields, metadata keys (usage, duration, model), assignee present
- **Harness coverage**: All expected harnesses exercised, each produced ODIN-STATUS SUCCESS
- **Status lifecycle**: All transitions valid (no BACKLOG -> DONE skip), all tasks reached terminal status
- **DAG ordering**: Assembly task started EXECUTING only after all deps reached terminal status
- **Comment pipeline**: Every task has planning + execution comments, valid types
- **Token usage**: Positive input/output/total for every task, sum matches summary
- **Cost estimation**: Every task's model has pricing, produces numeric cost > 0, spec total = sum of task costs

### Capturing a snapshot

```bash
# 1. Run the spec (regular terminal, not Claude Code)
cd odin/temp_test_dir/
odin plan --quick ../sample_specs/full_harness_smoke_spec.md

# 2. Find the spec ID
odin specs

# 3. Extract
cd taskit/taskit-backend/
python testing_tools/snapshot_extractor.py <spec_id> ../../tests/e2e_snapshots/<name>

# 4. Write tests — copy test_full_harness_smoke.py as template
# 5. Verify
python -m pytest tests/e2e_snapshots/ -v
```

### When to capture

- New harness added
- Bug fix for pipeline issue (regression guard)
- DAG executor changes
- Comment/proof pipeline changes
- New feature (questions, reflection)

### Cost estimation tests

Snapshot tests validate the cost pipeline end-to-end:

```bash
# Backend snapshot cost tests
python -m pytest tests/e2e_snapshots/test_full_harness_smoke.py::TestCostEstimation -v

# Frontend cost formatter tests
cd taskit/taskit-frontend && npm run test:run -- --reporter=verbose costEstimation
```

## Running a Live E2E

```bash
cd odin/temp_test_dir/
odin plan --quick ../sample_specs/full_harness_smoke_spec.md
```

### Verifying success

1. Run log exists: `.odin/logs/run_<timestamp>.jsonl` with `plan_completed` entry
2. Task logs exist: `taskit/taskit-backend/logs/spec_<SPEC_ID>_task_*.log`
3. Summaries show success: `grep SUMMARY taskit/taskit-backend/logs/spec_<SPEC_ID>_*.log`
4. Snapshot tests pass: `python -m pytest tests/e2e_snapshots/ -v`

### Finding logs

| What | Where |
|------|-------|
| Odin run log | `.odin/logs/run_<TIMESTAMP>.jsonl` |
| Task execution logs | `taskit/taskit-backend/logs/spec_<SPEC_ID>_task_<TASK_ID>.log` |
| App log | `taskit/taskit-backend/logs/taskit.log` |
| App log (full tracebacks) | `taskit/taskit-backend/logs/taskit_detail.log` |
| MCP server log | `.odin/logs/mcp_server.log` |

### Debugging a failed run

1. `python testing_tools/spec_trace.py <spec_id>` — see task status, deps, problems
2. `python testing_tools/task_inspect.py <task_id>` — deep-dive the failing task
3. If reflection: `python testing_tools/reflection_inspect.py <report_id>`
4. Capture broken state: `python testing_tools/snapshot_extractor.py <spec_id> ../../tests/e2e_snapshots/debug_<issue>`
5. Write regression test, fix code

## Common Issues

| Symptom | Cause | Fix |
|---|---|---|
| `duplicate session: odin-<id>` | Stale tmux session | `tmux kill-session -t odin-<id>` |
| Task stuck in IN_PROGRESS | Deps not met or Celery not running | Check `celery -A config worker` |
| `MCP ERROR (taskit)` but tool succeeds | MCP host treating stderr as errors | Check `.odin/logs/mcp_server.log` |
| MiniMax/GLM task has no proof comments | opencode.json missing `permission` block | Fixed: `_format_mcp_opencode` includes permission |
