# Task Preset TDD Enforcement — Debug Guide

**Status: PROPOSED** — debugging patterns for when this flow is implemented.

## Symptom → Cause Map

| Symptom | Likely Cause | Where to Look |
|---------|-------------|---------------|
| Planner outputs no `task_type` fields | Prompt instruction missing or too weak | `orchestrator.py` :: `_build_plan_prompt()` — check PRESET DECOMPOSITION section exists |
| All tasks are `standalone` | Planner ignoring decomposition instruction | Check plan JSON output; may need stronger prompt or few-shot examples |
| `test` task passes (should fail) | Agent wrote tautological tests or mocked everything | Proof comment content — look for mock-heavy tests or `assert True` |
| `implement` task fails with "no test files" | Upstream test task proof missing `file_paths` | `_build_task_context()` — check test task's proof comment has file_paths attachment |
| Gate rejects task but agent's proof looks correct | Gate parsing logic doesn't match agent's output format | `_verify_preset_gate()` — check regex/parsing against actual proof text |
| `implement` task has full codebase context (should be isolated) | `_wrap_prompt()` not filtering for `test` preset | Check preset branching in `_wrap_prompt()` — test preset must EXCLUDE impl context |
| Reflection doesn't mention preset criteria | Preset-specific criteria not injected into reflection prompt | `reflection.py` :: `build_reflection_prompt()` — check `_preset_review_criteria()` called |
| test→implement dependency not created | Planner used wrong symbolic IDs in `depends_on` | Plan JSON — verify implement task's depends_on includes test task ID |
| Task executes despite gate failure | Gate result not checked before status transition | `_execute_task()` — verify `_verify_preset_gate()` called and result checked |
| Spec with `## Scenarios` section ignored | Scenario parser not implemented or not connected to planner | `specs.py` — check SpecArchive extracts scenarios; `_build_plan_prompt()` — check scenarios forwarded |

## Verification Sequence (for testing the feature itself)

### 1. Planning produces correct decomposition

```bash
# Plan a spec with a multi-feature requirement
odin plan test_specs/profile_endpoint.md --dry-run

# Check: output JSON should have task_type fields
# Check: test tasks should have no depends_on to implement tasks
# Check: implement tasks should depend on corresponding test tasks
# Check: complexity "low" tasks should be standalone/scaffold
```

### 2. Context isolation works

```bash
# Execute a test preset task, then inspect what context it received
odin exec <test_task_id> --verbose

# Check agent's system prompt: should NOT contain model/view/serializer code
# Check agent's system prompt: SHOULD contain behavioral requirements + test patterns

# Compare with implement preset task
odin exec <impl_task_id> --verbose

# Check agent's system prompt: SHOULD contain test file contents from upstream
# Check agent's system prompt: SHOULD contain full codebase context
```

### 3. Gates enforce correctly

```bash
# For test preset: agent should report failing tests in proof
python testing_tools/task_inspect.py <test_task_id> --json --sections comments

# Look for proof comment with:
#   - file_paths: [test file paths]
#   - test output showing failures

# For implement preset: agent should report passing tests in proof
python testing_tools/task_inspect.py <impl_task_id> --json --sections comments

# Look for proof comment with:
#   - test output showing all pass
#   - build output showing success
```

### 4. Reflection uses preset criteria

```bash
# After reflection runs, check the reflection report
python testing_tools/reflection_inspect.py <report_id> --sections verdict,diagnosis

# For test preset tasks: should mention test coverage, behavior vs implementation
# For implement preset tasks: should mention over-engineering, minimum code
```

## Common Failure Modes During Development

### Gate parsing is fragile

The verification gate parses the agent's proof comment text for test results. Different agents format output differently. Common formats to handle:

```
# pytest output
FAILED tests/test_profile.py::test_get_profile - AssertionError
=== 4 failed, 0 passed ===

# jest output
Tests: 4 failed, 0 passed
Test Suites: 1 failed

# go test output
FAIL	./profile	0.003s

# generic
✗ 4 tests failed
```

If gate parsing fails, it should default to PASS (don't block execution on parsing bugs) and log a warning.

### Context isolation leaks

The test preset must exclude implementation context. But "implementation context" depends on the project. Sources of leaks:
- Upstream scaffold task's proof might describe implementation structure
- Working directory file listing could reveal existing implementations
- Agent might `cat` files on its own during execution

Mitigation: The context isolation is in the PROMPT, not in file system access. The agent CAN read files — the isolation prevents the PROMPT from giving implementation hints that bias the tests. This is "nudge isolation" not "sandbox isolation."

### Planner over-decomposes

If the planner creates test→implement pairs for trivial tasks (rename a variable, add an import), it adds overhead without benefit.

Fix: Strengthen the prompt rule — "Only decompose tasks with complexity medium or high. Tasks with complexity low should be standalone."

### Test files not forwarded to implement task

The implement task needs the actual test file contents, not just a summary. If `_build_task_context()` only forwards the proof summary (current behavior), the implement agent doesn't know what tests to satisfy.

Fix: For implement preset, `_build_task_context()` must read file contents from paths listed in upstream test task's proof `file_paths`.

## Env Vars (Proposed)

| Variable | Effect | Default |
|----------|--------|---------|
| `ODIN_PRESET_ENFORCEMENT` | Enable/disable preset gates | `true` |
| `ODIN_DEFAULT_PRESET` | Default preset when planner omits task_type | `standalone` |
| `ODIN_TEST_GATE_STRICT` | If true, gate independently runs tests (not just parses proof) | `false` |

## Log Locations (when implemented)

| Layer | Log | What's in it |
|-------|-----|-------------|
| Planning | `.odin/logs/plan_*.jsonl` | Planner prompt, raw LLM output, parsed task_type fields |
| Execution | `.odin/logs/run_*.jsonl` | Preset detection, context isolation decisions, gate results |
| Gate | `.odin/logs/run_*.jsonl` | Gate check details: what was parsed, pass/fail, reason |
| Reflection | TaskIt reflection reports | Preset-specific criteria evaluation |

## Quick Commands (for post-implementation debugging)

```bash
# Check what preset a task was assigned
python testing_tools/task_inspect.py <task_id> --json --sections basic | python -c "import sys,json; d=json.load(sys.stdin); print(d.get('metadata',{}).get('preset','none'))"

# Find all test preset tasks in a spec
python testing_tools/spec_trace.py <spec_id> --json | python -c "import sys,json; d=json.load(sys.stdin); [print(t['id'],t['title']) for t in d.get('tasks',[]) if t.get('metadata',{}).get('preset')=='test']"

# Check if test→implement dependency chain is correct
python testing_tools/spec_trace.py <spec_id> --sections tasks,dependencies

# Verify gate result for a task
grep "preset_gate" .odin/logs/run_*.jsonl | python -m json.tool
```
