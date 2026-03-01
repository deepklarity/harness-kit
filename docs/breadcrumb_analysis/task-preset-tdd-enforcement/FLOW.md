# Task Preset TDD Enforcement — Proposed Flow

**Status: PROPOSED** — not yet implemented. Documents the target design.

Trigger: `odin plan <spec>` (planning phase auto-decomposes tasks using presets)
End state: Every feature task in the DAG has structural TDD enforcement via preset-typed task pairs with context isolation and verification gates.

## Problem Statement

Harness-kit's CLAUDE.md philosophy mandates test-first wave execution with scope isolation. But `odin plan` + `odin exec` don't enforce any of this on projects they build. The planner creates flat "build feature X" tasks. Agents receive full context and write whatever they want. No structural separation between test-writing and implementation.

The fix: **task presets** — typed task templates that carry their own context injection rules, verification gates, and prompt additions. The planner emits preset-typed tasks; the executor enforces preset-specific gates.

## Task Presets

Each preset defines: what context the agent sees, what verification must pass, and what prompt additions are injected.

| Preset | Purpose | Context Isolation | Verification Gate |
|--------|---------|-------------------|-------------------|
| `test` | Write failing tests from behavioral spec | Behavioral requirements + test patterns. NO implementation context. | Tests must FAIL (no false positives) |
| `implement` | Make tests pass | Upstream test files + full codebase | Tests must PASS + build must succeed |
| `scaffold` | Project structure, config, boilerplate | Full codebase | Build must succeed |
| `integrate` | Wire components, connect layers | Full codebase + upstream proofs | Integration tests must pass |
| `verify` | End-to-end verification, visual checks | Full codebase + all upstream proofs | Screenshots + manual checklist |
| `standalone` | Today's behavior — no preset enforcement | Full codebase | Build must succeed (current default) |

## Proposed Flow

### Phase 1: Planning — Preset-Aware Decomposition

```
User runs: odin plan my_spec.md
                    |
orchestrator.py :: plan_spec()
  → reads spec content from SpecArchive
  → calls _build_plan_prompt()
                    |
_build_plan_prompt()
  → [NEW] injects PRESET DECOMPOSITION section into planner prompt
  → instructs LLM: "For each feature task, emit a test→implement pair"
  → provides preset schema: task_type field with allowed values
  → provides examples of correct decomposition
                    |
LLM planner output (JSON array)
  → [NEW] each task has "task_type" field (default: "standalone")
  → test tasks have depends_on: [] or [scaffold tasks only]
  → implement tasks have depends_on: [corresponding test task]
                    |
orchestrator.py :: _create_tasks_from_plan()
  → [NEW] validates task_type field against allowed presets
  → [NEW] stores task_type in task.metadata["preset"]
  → creates dependency edges (test→implement pairs linked)
  → posts tasks to TaskIt
```

### Phase 2: Execution — Preset-Gated Dispatch

```
User runs: odin exec <task_id>
                    |
orchestrator.py :: exec_task()
  → checks dependencies (existing behavior)
  → [NEW] reads task.metadata["preset"] to determine task type
                    |
                    ├── [preset=test]
                    │   orchestrator.py :: _wrap_prompt()
                    │     → [NEW] injects TEST PRESET context:
                    │       - Behavioral requirements from spec
                    │       - Existing test patterns (discovered via file scan)
                    │       - EXCLUDES: model schemas, view code, serializer code
                    │       - Adds gate: "Run tests → they must FAIL"
                    │     → agent writes test files
                    │     → [NEW] _verify_preset_gate("test")
                    │       - Parses agent's proof for test execution output
                    │       - Checks: all new tests failed (exit code != 0)
                    │       - If tests PASS → mark task NEEDS_WORK ("false positive tests")
                    │
                    ├── [preset=implement]
                    │   orchestrator.py :: _wrap_prompt()
                    │     → [NEW] injects IMPLEMENT PRESET context:
                    │       - Full codebase context
                    │       - Test files from upstream test task (via _build_task_context)
                    │       - Adds gate: "Run tests → they must PASS"
                    │     → agent writes implementation
                    │     → [NEW] _verify_preset_gate("implement")
                    │       - Parses agent's proof for test + build output
                    │       - Checks: all tests pass AND build succeeds
                    │       - If tests FAIL → mark task NEEDS_WORK
                    │
                    ├── [preset=scaffold]
                    │   orchestrator.py :: _wrap_prompt()
                    │     → standard context (no isolation)
                    │     → Adds gate: "Build must succeed"
                    │     → agent creates project structure
                    │     → _verify_preset_gate("scaffold")
                    │       - Build succeeds
                    │
                    ├── [preset=integrate]
                    │   orchestrator.py :: _wrap_prompt()
                    │     → full context + all upstream proofs
                    │     → Adds gate: "Integration tests must pass"
                    │     → _verify_preset_gate("integrate")
                    │       - Integration test suite passes
                    │
                    ├── [preset=verify]
                    │   orchestrator.py :: _wrap_prompt()
                    │     → full context + all upstream proofs
                    │     → Adds gate: "Screenshots required + checklist"
                    │     → _verify_preset_gate("verify")
                    │       - Screenshots attached to proof
                    │       - All checklist items addressed
                    │
                    └── [preset=standalone] (or no preset)
                        → today's behavior unchanged
```

### Phase 3: Reflection — Preset-Aware Review

```
reflection.py :: reflect_task()
  → [NEW] reads task.metadata["preset"]
  → [NEW] injects preset-specific review criteria into reflection prompt:
    - test preset: "Did the tests cover edge cases? Are they testing behavior, not implementation?"
    - implement preset: "Does the code ONLY satisfy the tests? No over-engineering?"
    - scaffold preset: "Is the structure idiomatic? Does it follow project conventions?"
    - integrate preset: "Are all seams properly connected? Error handling at boundaries?"
  → reflection agent evaluates with preset-aware criteria
  → verdict: PASS / NEEDS_WORK / FAIL
```

## Example: Feature Decomposition

Spec: "Add a user profile endpoint with avatar upload"

Planner produces:

```
T1  [scaffold]    Set up file structure for profile module
T2a [test]        Define profile endpoint behavior (depends_on: [T1])
T2b [implement]   Implement profile endpoint (depends_on: [T2a])
T3a [test]        Define avatar upload behavior (depends_on: [T1])
T3b [implement]   Implement avatar upload (depends_on: [T3a])
T4  [integrate]   Wire profile + avatar into main app (depends_on: [T2b, T3b])
T5  [verify]      End-to-end verification with screenshots (depends_on: [T4])
```

DAG execution order:
```
Wave 1: T1 (scaffold)
Wave 2: T2a, T3a (test — parallel, both depend only on T1)
Wave 3: T2b, T3b (implement — parallel, each depends on its test task)
Wave 4: T4 (integrate)
Wave 5: T5 (verify)
```

Key structural properties:
- T2a cannot see T2b's implementation context (scope isolation)
- T2b cannot execute until T2a's tests exist and fail (dependency gate)
- T3a and T3b are independent of T2a and T2b (parallel waves)
- T4 runs only after both features are implemented (integration gate)
- T5 is the human-checkable end state

## Cross-references

- Current planning flow: `odin-plan-mode/FLOW.md`
- Current execution flow: `spec-task-lifecycle/02-execute-and-dispatch/FLOW.md`
- Current reflection flow: `spec-task-lifecycle/03-reflection-loop/FLOW.md`
- Task proof submission: `task-proof-submission/FLOW.md`
