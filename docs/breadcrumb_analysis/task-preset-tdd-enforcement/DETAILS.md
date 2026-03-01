# Task Preset TDD Enforcement — Detailed Trace

**Status: PROPOSED** — maps proposed changes to exact locations in the current codebase.

## 1. Task Schema Extension

**File**: `odin/src/odin/taskit/models.py`
**Current**: Lines 30-44 (Task Pydantic model)

**Change**: Add `task_type` / preset concept. Two implementation options:

**Option A — metadata field (minimal change)**:
Store preset in existing `metadata: Dict[str, Any]` as `metadata["preset"]`.
- Pro: No schema migration, backwards compatible
- Con: No validation at model level, easy to typo

**Option B — first-class field (recommended)**:
Add `TaskPreset` enum and field to Task model.

```python
# New enum (models.py, after TaskStatus)
class TaskPreset(str, Enum):
    TEST = "test"
    IMPLEMENT = "implement"
    SCAFFOLD = "scaffold"
    INTEGRATE = "integrate"
    VERIFY = "verify"
    STANDALONE = "standalone"

# New field on Task model
class Task(BaseModel):
    ...
    preset: TaskPreset = TaskPreset.STANDALONE  # backwards compatible default
```

**TaskIt backend mirror**: `taskit/taskit-backend/tasks/models.py` needs matching field on the Django Task model (lines 134-150). Add as a CharField with choices, default="standalone".

**Serializer update**: `taskit/taskit-backend/tasks/serializers.py` — add `preset` to TaskReadSerializer and TaskWriteSerializer.

---

## 2. Plan Prompt — Preset Decomposition Instructions

**File**: `odin/src/odin/orchestrator.py`
**Function**: `_build_plan_prompt()` (lines 416-498)
**Called by**: `plan_spec()` → `_plan_with_agent()`

**What to add** (after the existing PROOF-FIRST DECOMPOSITION section, ~line 480):

A new section: `PRESET-AWARE DECOMPOSITION`

Content:
- Explain the 6 presets and when to use each
- Instruct planner: "For each feature task that produces code, emit a `test` task and an `implement` task"
- The `test` task description should contain ONLY behavioral requirements (what the code should do, not how)
- The `implement` task description should reference the test task: "Implement to pass the tests from T{n}a"
- Scaffold, integrate, verify, standalone tasks don't need pairing
- Add `task_type` to the JSON output schema (alongside existing `id`, `title`, `description`, etc.)

**Example to embed in prompt**:

```
GOOD decomposition:
  T1  [scaffold]    "Set up Express project with test runner configured"
  T2a [test]        "Write tests for user registration: success, duplicate email, weak password, missing fields"
  T2b [implement]   "Implement registration endpoint to pass tests from T2a" (depends_on: T2a)

BAD decomposition:
  T1 [standalone]   "Build user registration with validation and tests"
  (agent writes implementation first, adds token tests after — defeats TDD)
```

**Decision**: Whether the planner ALWAYS decomposes into test→implement pairs, or only when the spec/task is complex enough. Recommendation: always decompose for tasks with complexity "medium" or "high". Tasks with complexity "low" (mechanical, config-only) stay as `standalone` or `scaffold`.

---

## 3. Task Creation — Preset Validation

**File**: `odin/src/odin/orchestrator.py`
**Function**: `_create_tasks_from_plan()` (lines 511-609)
**Called by**: `plan_spec()` after LLM returns plan JSON

**Current behavior**: Parses JSON array, creates Task objects, resolves symbolic depends_on to real IDs.

**Change**:
- Extract `task_type` from each plan item (default: "standalone")
- Validate against allowed preset values
- Store in `task.metadata["preset"]` (Option A) or `task.preset` (Option B)
- **Validation rule**: If a `test` task exists, there should be a corresponding `implement` task that depends on it. Warn (don't fail) if orphan test tasks exist.
- **Validation rule**: An `implement` task should have at least one `test` task in its `depends_on`. Warn if not.

---

## 4. Prompt Wrapping — Context Isolation per Preset

**File**: `odin/src/odin/orchestrator.py`
**Function**: `_wrap_prompt()` (lines 1994-2093, static method)
**Called by**: `_execute_task()` → harness execution

**Current behavior**: Appends working directory, TaskIt MCP instructions, Chrome DevTools section, Mobile section. Same context for all tasks.

**Change**: Branch on preset type. New parameter: `preset: str = "standalone"`.

### test preset injection

```
CONTEXT BOUNDARY — TEST PRESET
You are writing tests for a feature that does not exist yet.

YOUR CONTEXT (what you can use):
- The behavioral requirements below
- The project's existing test patterns and conventions
- The test framework and assertion library already in use

EXCLUDED (do NOT reference or assume):
- Implementation details (models, views, serializers, handlers)
- Database schema beyond what's in the behavioral requirements
- Internal function signatures or class hierarchies

YOUR TASK:
1. Write test cases covering: happy paths, edge cases, error cases
2. Run the tests — they MUST FAIL (the feature doesn't exist)
3. If any test passes, it's a false positive — fix or delete it
4. Submit test file paths in your proof comment

VERIFICATION GATE: All new tests must fail with meaningful assertion errors.
A test that passes before implementation exists is testing nothing.
```

### implement preset injection

```
CONTEXT BOUNDARY — IMPLEMENT PRESET
You are implementing a feature to satisfy existing failing tests.

THE TESTS (from upstream task):
{upstream_test_file_contents}

YOUR TASK:
1. Read the test files carefully — they define the behavioral contract
2. Write the minimum code to make all tests pass
3. Run the tests — they MUST PASS
4. Run the project build — it MUST succeed
5. Submit implementation file paths + test output in your proof comment

VERIFICATION GATE: All tests pass AND build succeeds.
Do not add features beyond what the tests require.
```

### scaffold preset injection

```
CONTEXT BOUNDARY — SCAFFOLD PRESET
You are setting up project structure, configuration, or boilerplate.

YOUR TASK:
1. Create the requested structure
2. Run the project build — it MUST succeed
3. Submit file paths in your proof comment

VERIFICATION GATE: Build succeeds. Structure is idiomatic for the project.
```

### integrate preset injection

```
CONTEXT BOUNDARY — INTEGRATE PRESET
You are wiring components together that were built in upstream tasks.

UPSTREAM WORK:
{upstream_proof_summaries}

YOUR TASK:
1. Connect the components as described
2. Run integration tests — they MUST pass
3. Run the full build — it MUST succeed
4. Submit proof with integration test output

VERIFICATION GATE: Integration tests pass AND build succeeds.
```

### verify preset injection

```
CONTEXT BOUNDARY — VERIFY PRESET
You are performing end-to-end verification of completed work.

UPSTREAM WORK:
{all_upstream_proof_summaries}

YOUR TASK:
1. Run the full application
2. Verify each feature works as specified
3. Take screenshots of key states
4. Submit proof with screenshots and verification checklist

VERIFICATION GATE: Screenshots attached. All checklist items confirmed.
```

---

## 5. Upstream Context Injection — Test File Forwarding

**File**: `odin/src/odin/orchestrator.py`
**Function**: `_build_task_context()` (lines 959-1119)
**Called by**: `exec_task()` before prompt wrapping

**Current behavior**: Injects summaries from completed upstream tasks (reflection feedback, proofs, Q&A).

**Change for implement preset**: When building context for an `implement` task whose upstream includes a `test` task:
- Read the test files created by the test task (paths available in proof comment's `file_paths`)
- Inject full test file contents (not just summary) into the implement task's prompt
- This is the structural equivalent of CLAUDE.md's "the implementation subagent receives the failing test files"

**Implementation**: In `_build_task_context()`, after existing upstream context gathering:
```python
# For implement tasks, inject test file contents from test dependencies
if task_preset == "implement":
    for dep in upstream_tasks:
        if dep.metadata.get("preset") == "test":
            test_files = dep.proof_comment.file_paths  # from proof attachment
            for path in test_files:
                content = read_file(working_dir / path)
                context += f"\n--- Test file: {path} ---\n{content}\n"
```

---

## 6. Verification Gates — Post-Execution Checks

**File**: New function in `odin/src/odin/orchestrator.py`
**Function**: `_verify_preset_gate()` (new)
**Called by**: `_execute_task()` after agent completes, before status transition

This is the enforcement layer. After the agent posts its proof, before transitioning to REVIEW:

```python
async def _verify_preset_gate(self, task: Task, proof_comment: Comment) -> GateResult:
    """Check preset-specific verification criteria."""
    preset = task.metadata.get("preset", "standalone")

    if preset == "test":
        # Parse proof for test output
        # Check: tests were run AND they failed
        # If tests passed → GateResult(passed=False, reason="false positive tests")

    elif preset == "implement":
        # Parse proof for test output + build output
        # Check: tests pass AND build succeeds
        # If tests fail → GateResult(passed=False, reason="tests still failing")

    elif preset in ("scaffold", "integrate"):
        # Check: build succeeds

    elif preset == "verify":
        # Check: screenshots attached to proof

    else:  # standalone
        # Current behavior (build check only, if present)

    return GateResult(passed=True)
```

**Gate failure handling**: If gate fails, mark task as NEEDS_WORK (not FAILED — the agent should retry with feedback about what gate it failed). Inject gate failure reason into reflection context.

**Important nuance**: The gate checks the agent's SELF-REPORTED proof (test output in the proof comment). It doesn't independently run tests — that would require knowing the project's test command. Future enhancement: allow specs or project config to declare `test_command` and `build_command` for independent verification.

---

## 7. Reflection — Preset-Aware Review Criteria

**File**: `odin/src/odin/reflection.py`
**Function**: `build_reflection_prompt()` (lines 25-108)
**Called by**: `reflect_task()`

**Current behavior**: Generic quality assessment prompt for all tasks.

**Change**: Add preset-specific review criteria section after the generic quality assessment.

```python
def _preset_review_criteria(preset: str) -> str:
    criteria = {
        "test": """
### Preset: Test — Additional Review Criteria
- Do tests cover happy path, edge cases, AND error cases?
- Are tests testing BEHAVIOR (what the code does) not IMPLEMENTATION (how it does it)?
- Would these tests catch a regression if the implementation changed internally?
- Are there any tautological tests (assert True, mock returns X → assert X)?
- Did the agent confirm tests FAIL before submission?
""",
        "implement": """
### Preset: Implement — Additional Review Criteria
- Does the implementation ONLY satisfy the tests? No gold-plating?
- Are there features or abstractions beyond what the tests require?
- Did the agent confirm tests PASS after implementation?
- Is the code the MINIMUM needed to pass, or is it over-engineered?
""",
        "integrate": """
### Preset: Integrate — Additional Review Criteria
- Are all component seams properly connected?
- Is error handling present at integration boundaries?
- Do integration tests cover the critical paths?
""",
    }
    return criteria.get(preset, "")
```

---

## 8. Planner Defaults — Smart Decomposition Rules

**File**: `odin/src/odin/orchestrator.py`
**Function**: `_build_plan_prompt()` (lines 416-498)

**Rules for the planner** (embedded in prompt, not in code):

1. **Always decompose** tasks with complexity "medium" or "high" into test→implement pairs
2. **Never decompose** tasks that are purely structural (config files, directory setup, CI pipeline) — use `scaffold`
3. **Use `integrate`** when a task's primary job is connecting components from other tasks
4. **Use `verify`** for the final task in a spec that does end-to-end checking
5. **Default to `standalone`** when none of the above apply (backwards compatible)

**Planner should NOT decompose when**:
- The task is "low" complexity (mechanical, config-only)
- The task doesn't produce testable code (documentation, CI config, env setup)
- The spec explicitly says to skip decomposition (escape hatch)

---

## 9. Spec Format — Optional Scenarios Section

**File**: `odin/src/odin/specs.py` (SpecArchive model, lines 15-24)

**Current behavior**: Spec is free-form markdown. No structured sections.

**Optional enhancement**: Allow specs to include a `## Scenarios` section:

```markdown
# My Feature Spec

## Description
Add user profile endpoint with avatar upload.

## Scenarios
- Happy: authenticated user retrieves own profile → 200 with all fields
- Happy: user uploads JPEG avatar under 5MB → 200 with avatar_url
- Edge: profile with no avatar → 200 with avatar_url=null
- Edge: upload exactly 5MB file → 200 (boundary case)
- Error: unauthenticated request → 401
- Error: upload 10MB file → 413
- Error: upload .exe file → 400

## Requirements
...
```

If `## Scenarios` is present, the planner feeds them directly into `test` task descriptions instead of generating its own. If absent, planner generates scenarios from the narrative (current behavior, just more structured).

This follows Default First — specs work without scenarios. The section is a power-user override.

---

## Files Changed Summary

| File | Change | Scope |
|------|--------|-------|
| `odin/src/odin/taskit/models.py` | Add TaskPreset enum, preset field | Schema |
| `odin/src/odin/orchestrator.py` :: `_build_plan_prompt()` | Add preset decomposition instructions | Planning |
| `odin/src/odin/orchestrator.py` :: `_create_tasks_from_plan()` | Parse + validate task_type field | Planning |
| `odin/src/odin/orchestrator.py` :: `_wrap_prompt()` | Branch context injection by preset | Execution |
| `odin/src/odin/orchestrator.py` :: `_build_task_context()` | Forward test files to implement tasks | Execution |
| `odin/src/odin/orchestrator.py` :: `_verify_preset_gate()` | New function — post-execution gate checks | Execution |
| `odin/src/odin/reflection.py` :: `build_reflection_prompt()` | Add preset-specific review criteria | Reflection |
| `taskit/taskit-backend/tasks/models.py` | Add preset field to Django Task model | Backend |
| `taskit/taskit-backend/tasks/serializers.py` | Expose preset in API | Backend |
| `odin/src/odin/specs.py` | (Optional) Parse ## Scenarios section | Specs |
