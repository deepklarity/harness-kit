# Test Case Process and Philosophy

This document defines the testing framework, process, and mindset for harness-kit. It is written for both humans and AI agents working in this codebase.

## The Core Problem This Document Solves

**Tests pass. Feature doesn't work.** This is the most common failure mode in any test suite. Static tests validate frozen assumptions about code behavior — they do not validate that a user can see and use the feature. The gap between "test passes" and "feature works" is where bugs live in production.

This document creates tight, explicit loops to close that gap.

---

## 1. Test Categories: What Each Proves

Every test in this codebase belongs to exactly one category. Each category has a different **proof strength** — what it can and cannot guarantee.

| Category | Proof Strength | What It Proves | What It Cannot Prove |
|----------|---------------|----------------|---------------------|
| **Unit** | Logic correctness | A function produces correct output for given input | That the function is called correctly in context |
| **Mock** | Boundary behavior | Code handles external boundaries (APIs, CLIs, subprocesses) correctly *given assumed responses* | That the real external system responds as assumed |
| **Disk** | I/O correctness | Files are read/written/parsed correctly | That the file system state is what you expect at runtime |
| **Snapshot** | Contract stability | Field shapes, lifecycle transitions, and pipeline invariants haven't drifted since the snapshot was captured | That the current live system matches the snapshot |
| **Integration** | End-to-end behavior | Real services interact correctly | Nothing — this is the ground truth |
| **Live verification** | User-visible behavior | The feature works as the user would experience it | Nothing — this is the ultimate ground truth |

**Critical understanding:** Each layer proves something the layer above cannot. Unit tests cannot prove integration works. Snapshots cannot prove the live system matches. **Only live verification proves the feature works.**

### The Proof Chain

```
Unit test passes        → "The function logic is correct"
Mock test passes        → "The boundary handling is correct, assuming the mock is accurate"
Snapshot test passes    → "The data contract hasn't drifted, assuming the snapshot is current"
Integration test passes → "The real services work together"
Live verification passes → "The user can see and use it"
```

Each link in this chain can break independently. A passing unit test means nothing if the mock it depends on is stale. A passing snapshot test means nothing if the snapshot was captured before the latest model change.

---

## 2. When Something Breaks: RCA Protocol

When a bug is found — whether by a user, a test, or an agent — follow this protocol **in order**. Do not skip steps.

### Step 1: Reproduce

Before anything else, confirm the bug exists and define its boundaries.

```
REPRODUCE CHECKLIST:
[ ] Can I see the bug? (UI, API response, log output, test failure)
[ ] What is the exact input that triggers it?
[ ] What is the expected behavior?
[ ] What is the actual behavior?
[ ] Is it deterministic or intermittent?
[ ] Which environment? (local, staging, specific browser, specific agent)
```

**If you cannot reproduce it, you cannot fix it.** Do not guess. Do not "fix" something you haven't seen fail.

### Step 2: Locate

Use the diagnostic tools to narrow the failure to a specific layer.

```
LOCATE — run in order, stop when you find the discrepancy:

1. Diagnostic scripts (if data-related):
   python testing_tools/task_inspect.py <task_id> --brief
   python testing_tools/spec_trace.py <spec_id> --brief
   → Do the scripts show the expected data?
   → If NO: the bug is in the backend (model, serializer, view, or pipeline)
   → If YES: the data is correct but not reaching the UI

2. API contract check:
   → Does the API response include the field/value?
   → If NO: serializer or view bug
   → If YES: the bug is in the frontend (rendering, state, or data flow)

3. Frontend inspection:
   → Is the component receiving the prop?
   → Is the component rendering it?
   → Is there a conditional hiding it?
```

### Step 3: Root Cause Analysis (5 Whys)

Once you've located the failing layer, ask "why" until you reach the root cause. The first answer is almost never the root cause.

```
Example A — "Feature X doesn't show on UI"

Why doesn't it show?    → The component isn't rendering it.
Why not?                → The prop is undefined.
Why?                    → The API response doesn't include the field.
Why?                    → The serializer wasn't updated when the model was extended.
Why not?                → No test validates serializer field completeness.

ROOT CAUSE: Missing serializer integration test.

Example B — "Task stuck in EXECUTING for 30 minutes"

Why is it stuck?        → The DAG executor never transitioned it.
Why not?                → The execution result was posted but returned HTTP 500.
Why?                    → The execution_processing code raised on unexpected token format.
Why?                    → A new harness returns OpenAI-style keys; parser expected Anthropic keys.
Why wasn't this caught? → Mock test used Anthropic-style keys only.

ROOT CAUSE: Mock doesn't cover the real harness output format.

Example C — "Cost shows $0.00 for all tasks"

Why $0.00?              → estimate_task_cost returns 0.
Why?                    → The pricing table has no entry for the model name.
Why?                    → seedmodels added the model but not pricing fields.
Why not?                → Pricing fields are optional in agent_models.json schema.
Why is that ok?         → It wasn't — no validation test for pricing completeness.

ROOT CAUSE: Missing pricing coverage test for new models.
```

The root cause is never "the code is wrong." The root cause is always "what process or test gap allowed the wrong code to ship?"

### Step 4: Write the Failing Test FIRST

Before writing any fix, write a test that:
- Reproduces the exact bug
- Fails right now
- Will pass after the fix

This test is the **proof that the fix works**. Without it, you're guessing.

```python
# BAD: Fix first, test later (test confirms the fix, doesn't challenge it)
# GOOD: Test first, fix later (test defines the correct behavior, fix achieves it)
```

### Step 5: Fix and Verify

Fix the code. Run the failing test. It should pass. Run the full suite. Nothing else should break.

### Step 6: Verify Live

**This step is not optional.** After the fix passes all static tests, verify the behavior live:
- If it's a UI bug: load the page and confirm the feature works visually
- If it's an API bug: hit the endpoint and confirm the response
- If it's a pipeline bug: run a spec and confirm the output

### Step 7: Document

Record the RCA in the commit message or PR description:

```
Fix: [what was fixed]
Root cause: [the actual root cause, not the symptom]
Prevention: [what test/check now prevents recurrence]
```

If the bug reveals a systemic gap (e.g., "serializers are never tested for new fields"), create a follow-up to close the gap.

---

## 3. Static Tests vs. Live Verification

### Static tests (automated, run in CI or locally)

These run against **frozen state** — mocks, fixtures, snapshots, in-memory databases. They are fast, repeatable, and deterministic. They catch regressions.

**They do NOT prove the feature works in the real system.**

| Test type | Location | What's frozen |
|-----------|----------|---------------|
| Unit | `odin/tests/unit/` | Everything — pure input/output |
| Disk | `odin/tests/disk/` | Network/subprocess — only filesystem is real |
| Mock | `odin/tests/mock/` | External services — subprocess, HTTP, APIs |
| Backend Django | `taskit/taskit-backend/tests/` | Database (SQLite), auth (disabled) |
| Frontend Vitest | `taskit/taskit-frontend/src/**/*.test.*` | DOM (jsdom), API (mocked) |
| Snapshot | `tests/e2e_snapshots/` | All execution data (captured JSON) |

### Live verification (manual or semi-automated)

These run against **real services** — real database, real API, real LLMs, real browser.

| Verification | When | How |
|-------------|------|-----|
| API response check | After backend changes | Diagnostic scripts or `curl` with auth |
| UI visual check | After frontend or serializer changes | Open browser, navigate to feature |
| Spec execution | After harness, pipeline, or DAG changes | `odin plan --quick` with a sample spec |
| Full smoke test | Before release | Run `full_harness_smoke_spec.md`, capture snapshot, compare |

### The contract between them

Static tests and live verification serve different purposes. **Neither replaces the other.**

- Static tests catch **regressions** — "did this change break something that used to work?"
- Live verification catches **integration gaps** — "does this feature actually work end-to-end?"

A good testing process uses both in a tight loop:

```
Code change → Static tests pass → Live verification confirms → Ship
                  ↓ (if fail)            ↓ (if fail)
              Fix the code          Write a new static test that
                                    catches the gap, then fix
```

---

## 4. Stale Test Protocol

A stale test is a test that passes but no longer validates current behavior. It gives false confidence — the most dangerous kind of test.

### How tests become stale

| Cause | Example | Detection |
|-------|---------|-----------|
| **Model change not reflected in test** | New field added to Task but test fixture doesn't include it | Snapshot test catches missing fields; unit tests don't |
| **Mock diverged from reality** | Mock returns `{"tokens": 100}` but real API now returns `{"usage": {"tokens": 100}}` | Live verification fails where mock test passes |
| **Snapshot captured before a change** | Snapshot from spec run #38 doesn't include reflection fields added later | New invariant tests fail to cover new fields |
| **Test asserts format, not behavior** | `assert "cost" in response` passes even if cost value is wrong | Review — format tests should be flagged in PR review |
| **Test tests the mock, not the code** | Mock returns X, test asserts X was returned | Review — `assert mock_return_value == result` is always a smell |

### Detection methods

**Automated detection:**
- Run snapshot tests after model/serializer changes — they catch field drift
- Run `python -m pytest --co -q` periodically — tests that can't even collect are broken, not stale
- Compare snapshot `_meta.extracted_at` with recent model changes — old snapshots may be stale

**Manual detection (during PR review):**
- For every changed model field: is there a test that asserts this field exists in the API response?
- For every changed serializer: is there a snapshot that includes this serializer's output?
- For every mock: does the mock's return value match what the real system returns today?

### When a stale test is found

1. **Do not delete it.** The test was correct once — it caught something real.
2. **Update it** to reflect current behavior.
3. **If the test was testing the wrong thing** (format instead of behavior, mock instead of code), rewrite it to test the right thing.
4. **If the snapshot is stale**, re-capture: `python testing_tools/snapshot_extractor.py <spec_id> <dir>`
5. **Add a comment** to the PR: `"Updated stale test: [why it was stale, what changed]"`

### Communicating staleness

When you discover a stale test but can't fix it immediately (e.g., it requires a live spec run to re-capture a snapshot):

1. Mark it with `@pytest.mark.skip(reason="STALE: [description]. Needs re-capture after [condition].")`
2. Create a follow-up task with the specific remediation steps
3. Do NOT leave it silently passing — a skipped test is honest; a stale-but-passing test is a lie

---

## 5. Test Quality Standards

### What makes a test valuable

A test is valuable if deleting it would allow a bug to ship. Apply this check to every test:

```
"If I delete this test, what bug could reach the user?"
→ If the answer is "none" — the test is noise. Remove it.
→ If the answer is specific — the test is valuable. Keep it.
→ If the answer is vague — the test is probably testing the wrong thing. Rewrite it.
```

### Anti-patterns (with specific examples from this codebase)

**1. Testing your own mocks**
```python
# BAD: This tests unittest.mock, not your code
mock_api.get_task.return_value = {"status": "DONE"}
result = mock_api.get_task(42)
assert result["status"] == "DONE"  # You just tested the mock

# GOOD: Mock the boundary, test YOUR code's behavior
mock_api.get_task.return_value = {"status": "DONE"}
summary = your_function_that_uses_api(42)
assert summary.is_complete == True  # Tests YOUR logic given the boundary
```

**2. String-presence as sole validation**
```python
# BAD: Proves string concatenation works, not that the feature is correct
assert "estimated_cost" in response.data

# GOOD: Proves the value is correct
assert response.data["estimated_cost"] == 0.0042
```

**3. Happy-path only**
```python
# BAD: Only tests the simplest case
def test_cost():
    assert estimate_cost(1000, "claude-sonnet") == 0.003

# GOOD: Tests boundaries and failures too
def test_cost_zero_tokens():
    assert estimate_cost(0, "claude-sonnet") == 0.0

def test_cost_unknown_model():
    assert estimate_cost(1000, "nonexistent-model") is None

def test_cost_negative_tokens():
    assert estimate_cost(-1, "claude-sonnet") == 0.0
```

**4. Testing framework behavior**
```python
# BAD: Tests that Django ORM works (it does)
task = Task.objects.create(title="test")
assert Task.objects.get(pk=task.pk).title == "test"

# GOOD: Tests YOUR domain logic
task = Task.objects.create(title="test", status="TODO")
task.transition_to("IN_PROGRESS")
assert TaskHistory.objects.filter(task=task, field_name="status").exists()
```

### The Scenario Matrix

Before writing tests, produce a visible matrix. This is mandatory — not a suggestion.

```
## Scenario Matrix for [function/feature]

### What does this do in the real system?
[Who calls it, what downstream effect]

### Happy paths
- [Input] → [Expected output]

### Edge cases
- [Boundary input] → [Expected output]

### Failure modes
- [Bad input / error condition] → [Expected behavior]

### Integration seams
- [What upstream provides] → [What this code assumes] → [What downstream expects]
```

The matrix prevents the #1 test authoring failure: writing tests that confirm what you're about to implement, rather than tests that challenge whether the implementation is correct.

---

## 6. Test-Driven Bug Fix Workflow

When a bug is reported, this is the exact workflow:

```
1. REPRODUCE  → Can I see the bug?
2. LOCATE     → Which layer is failing? (diagnostic scripts → API check → frontend check)
3. RCA        → Why is it failing? (5 whys to root cause)
4. TEST       → Write a failing test that reproduces the bug
5. FIX        → Write the minimum code to pass the test
6. VERIFY     → Run full suite + live verification
7. DOCUMENT   → Record RCA in commit message
```

**Do not shortcut this.** The temptation is to jump from step 1 to step 5 ("I can see the bug, I know the fix"). That skips the test, which means:
- No proof the fix works
- No regression guard
- No documentation of what the bug was

### Worked examples

**A. UI not rendering a field**
```
1. REPRODUCE: Open task detail. Expected: field visible. Actual: not rendered.
2. LOCATE:    Diagnostic script shows data in DB ✓. API response missing the field ✗.
              → Bug is in the serializer.
3. RCA:       Serializer not updated when model was extended. No test for field completeness.
4. TEST:      test_serializer_includes_field() → fails.
5. FIX:       Add field to serializer → test passes.
6. VERIFY:    API now returns field ✓. UI renders it ✓.
7. DOCUMENT:  Root cause: serializer gap. Prevention: field completeness test.
```

**B. Pipeline data loss**
```
1. REPRODUCE: After spec run, task has no token usage in UI.
2. LOCATE:    task_inspect.py --json shows last_usage is empty.
              execution_result comment has raw output — envelope parsing failed.
3. RCA:       New harness returns OpenAI-style keys; parser expected Anthropic keys.
              Mock test only covered Anthropic format.
4. TEST:      test_extract_openai_style_usage() → fails.
5. FIX:       Handle both key formats in parser → test passes.
6. VERIFY:    Run spec with new harness → tokens captured ✓.
7. DOCUMENT:  Root cause: mock coverage gap. Prevention: parametrized test for all key formats.
```

**C. Cost displays as $0.00**
```
1. REPRODUCE: All tasks show $0.00 cost in UI.
2. LOCATE:    API returns estimated_cost_usd: 0. Backend pricing table has no entry for model.
3. RCA:       seedmodels added model without pricing fields. No validation test.
4. TEST:      test_all_models_have_pricing() → fails.
5. FIX:       Add pricing to agent_models.json → test passes.
6. VERIFY:    UI shows correct costs ✓.
7. DOCUMENT:  Root cause: schema allows priceless models. Prevention: pricing coverage test.
```

---

## 7. Test Maintenance Cadence

### After every code change

- Run the relevant test suite (unit for logic changes, integration for API changes, frontend for UI changes)
- If tests fail: fix the test OR fix the code — never skip failing tests

### After model/serializer changes

- Run snapshot tests: `python -m pytest tests/e2e_snapshots/ -v`
- If snapshots are stale: re-capture from a fresh spec run

### Weekly (or after significant changes)

- Run the full test collection check: `python -m pytest --collect-only 2>&1 | tail -5`
- If collection fails: tests are broken at import time — fix before they become invisible

### After a release or major feature merge

- Run a live smoke spec: `odin plan --quick sample_specs/full_harness_smoke_spec.md`
- Capture a new snapshot: `python testing_tools/snapshot_extractor.py <spec_id> <dir>`
- Compare against the previous snapshot for unexpected drift

---

## 8. Test Infrastructure Health

### Current state (as of 2026-02-24)

| Component | Collection Status | Run Status | Health |
|-----------|------------------|------------|--------|
| Odin unit/disk/mock | 506 tests collected | Passes | Healthy |
| Taskit backend | Collection fails (Django AppRegistry) | Cannot run | Broken — needs conftest.py fix |
| Taskit frontend | 97 tests collected | All pass | Healthy |
| E2E snapshots | 43 tests collected | Passes | Healthy |

**Taskit backend tests are currently broken at collection time.** This is the most dangerous state — tests that can't even run provide zero protection. Fixing this is a priority.

### Health indicators

| Indicator | Healthy | Warning | Critical |
|-----------|---------|---------|----------|
| Collection | All tests collect | Some imports fail | Entire suite won't collect |
| Pass rate | 100% | > 95% with known skip reasons | < 95% or unexplained failures |
| Snapshot age | < 1 week old | < 1 month old | > 1 month or pre-dates model changes |
| Coverage gaps | TEST_PLAN.md < 10% unchecked | 10-30% unchecked | > 30% unchecked |
| Live verification | Done after every feature | Done before releases | Never done |

---

## 9. Responsibilities by Role

### Developer (human or AI agent) making a change

1. Write the failing test before the fix
2. Run the relevant test suite after the change
3. If a model or serializer changed: run snapshot tests
4. If the change is user-visible: do live verification
5. If a test is stale: update or skip-with-reason, never delete silently

### Reviewer (human or AI agent)

1. For every changed model field: is there a test?
2. For every new feature: does the test matrix cover edge cases and failures?
3. For every mock: does it match reality?
4. Is there evidence of live verification?

### The system (CI, automated checks)

1. Run all collectable tests on every push
2. Flag tests that can't collect (import errors = broken tests)
3. Flag snapshots older than the latest model migration

---

## 10. Reference

### Test locations

| What | Where | Run with |
|------|-------|----------|
| Odin unit tests | `odin/tests/unit/` | `cd odin && python -m pytest tests/unit/ -v` |
| Odin disk tests | `odin/tests/disk/` | `cd odin && python -m pytest tests/disk/ -v` |
| Odin mock tests | `odin/tests/mock/` | `cd odin && python -m pytest tests/mock/ -v` |
| Odin integration | `odin/tests/integration/` | `cd odin && python -m pytest tests/integration/ -v` |
| Taskit backend | `taskit/taskit-backend/tests/` | `cd taskit/taskit-backend && USE_SQLITE=True FIREBASE_AUTH_ENABLED=False python manage.py test tests -v2` |
| Taskit frontend | `taskit/taskit-frontend/src/**/*.test.*` | `cd taskit/taskit-frontend && npm run test:run` |
| E2E snapshots | `tests/e2e_snapshots/` | `python -m pytest tests/e2e_snapshots/ -v` |

### Diagnostic scripts

```bash
cd taskit/taskit-backend
python testing_tools/task_inspect.py <task_id> [--brief|--json|--full] [--sections a,b]
python testing_tools/spec_trace.py <spec_id> [--brief|--json|--full] [--sections a,b]
python testing_tools/board_overview.py [board_id] [--brief|--json]
python testing_tools/reflection_inspect.py <report_id> [--brief|--json|--full] [--sections a,b]
python testing_tools/snapshot_extractor.py <spec_id> [output_dir] [--slim]
```

### Related documents

- `docs/philosophy/testing.md` — Testing principles (single source of truth, boundaries, anti-patterns)
- `docs/testing_process/testing_end_to_end.md` — E2E testing layers, snapshot workflow, debugging
- `odin/tests/TEST_PLAN.md` — Living coverage checklist
- `odin/tests/testcase_readme.md` — Complete test index
