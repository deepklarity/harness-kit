# Diagnostic scripts: inspect TaskIt data without the API

**Never use `curl`, raw API calls, or direct HTTP requests** to inspect TaskIt data. The API requires Firebase auth. **Always use the diagnostic scripts** in `taskit/taskit-backend/testing_tools/` — they use Django ORM directly (no auth needed) and produce richer output than the API.

### The scripts

All scripts run from `taskit/taskit-backend/`. Every script supports output modes for token efficiency:

```bash
cd taskit/taskit-backend

# Default (standard) — full human-readable output
python testing_tools/task_inspect.py <task_id>
python testing_tools/spec_trace.py <spec_id>
python testing_tools/board_overview.py [board_id]
python testing_tools/reflection_inspect.py <report_id>
python testing_tools/snapshot_extractor.py <spec_id> <output_dir>

# Brief — 1-3 lines: status + metrics + problem count (~5x fewer tokens)
python testing_tools/task_inspect.py <task_id> --brief
python testing_tools/spec_trace.py <spec_id> --brief

# JSON — structured output, most token-efficient for LLM consumption
python testing_tools/task_inspect.py <task_id> --json --sections basic,tokens

# Full — everything including full comment content and metadata
python testing_tools/task_inspect.py <task_id> --full

# Sections — cherry-pick which sections to include
python testing_tools/spec_trace.py <spec_id> --sections tasks,problems
python testing_tools/reflection_inspect.py <report_id> --sections verdict,diagnosis

# Slim snapshots — exclude large text fields for smaller golden files
python testing_tools/snapshot_extractor.py <spec_id> <output_dir> --slim
```

### When to reach for which script

| You want to... | Use this |
|---|---|
| Understand why a task failed or produced unexpected output | `task_inspect.py <task_id>` |
| Debug a spec run — see what happened, in what order, what broke | `spec_trace.py <spec_id>` |
| Check the state of a board after changes | `board_overview.py [board_id]` |
| Verify a reflection report's verdict, token usage, or quality | `reflection_inspect.py <report_id>` |
| Capture a known-good run as a regression test fixture | `snapshot_extractor.py <spec_id> <output_dir>` |
| Quick status check from LLM agent | Any script with `--brief` or `--json` |
| Check token usage and cost data for a task or spec | `task_inspect.py` (per-task) or `spec_trace.py` (aggregate) |

### Rule: scripts before code inspection

When debugging or verifying behavior, **run the relevant diagnostic script first** before reading source code or logs. The scripts surface problems automatically (invalid transitions, missing fields, empty sections, repeated text) and show the data in context.

For log tailing during live runs (not post-mortem debugging):
```bash
tail -f taskit/taskit-backend/logs/taskit.log           # App log
odin logs -f                                           # Follow all running tasks (from working dir)
odin logs debug -f                                     # Tail odin_detail.log (tracebacks)
odin logs -b <board_id>                                # Access logs from any directory
```
