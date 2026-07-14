# Testing Philosophy

This document captures the testing principles applied across the harness-kit codebase.

## Single Source of Truth for Derived Data

When the same value is computed in multiple places (e.g., cost in backend, frontend, and odin), they will inevitably diverge. The fix: compute once at the **authoritative source** (the backend, which has the canonical pricing table) and let consumers display.

**Concrete example — Cost Module:**
- Backend `pricing.py` is the single cost computation module
- `TaskSerializer.estimated_cost_usd` — computed at serializer time, sent with every task
- `SpecSerializer.cost_summary` — aggregated at serializer time, sent with every spec
- Frontend `formatCost()` — pure display formatter, no computation

**What to test where:**
- Backend: test that `estimate_task_cost()` produces correct values for all models (unit test the computation)
- Backend: test that API responses include cost fields with correct values (integration test the contract)
- Frontend: test that `formatCost()` produces correct display strings (unit test the formatter)
- Frontend: do NOT test cost computation — there is none; trust the backend contract

## Scenario Matrix

Before writing tests, produce a visible scenario matrix. This is not optional.

```
## Scenario Matrix for [feature/function name]

### What does this code ACTUALLY need to do?
[1-2 sentences: who calls it, what downstream effect]

### Happy paths
- [Scenario]: [input] → [expected output]

### Boundary & edge cases
- [Scenario]: [input] → [expected output]

### Failure modes
- [Scenario]: [input] → [expected behavior]

### The "delete test" check
- For each test: if I delete this test, what bug could ship?
```

## Test Boundaries

### Unit tests (fast, isolated)
- Pure functions: `estimate_task_cost()`, `formatCost()`, `compute_spec_cost_summary()`
- Model logic: status transitions, validation rules
- Transformers: data mapping functions

### Integration tests (test the contract)
- API endpoint responses: verify field presence, types, and values
- Serializer output: verify the shape of data crossing system boundaries
- Database queries: verify correct joins, filters, aggregations

### When NOT to test
- Don't test framework behavior (Django ORM, DRF serialization)
- Don't test your mocks (if a mock returns X and you assert X, you tested unittest.mock)
- Don't test string presence as the sole validation (`"cost" in output` proves concatenation, not correctness)

## Anti-Patterns

### Testing your own mocks
If the test sets up a mock to return X and then asserts X was returned, you've tested the mock library. Mocks simulate boundaries; assertions verify *your code's behavior* given that boundary.

### Happy-path-only coverage
If every test passes with the simplest input, the suite provides false confidence. The scenario matrix forces enumeration of what breaks.

### Testing format instead of semantics
Checking that a string "contains" a section header proves string interpolation works. Ask: if the header were wrong, would the behavior change? If not, the test is tautological.

### Frontend re-computing backend data
When the frontend re-derives values from raw data (e.g., computing cost from tokens + a pricing table), it creates a second source of truth that can diverge. If the backend already computes the value, the frontend should display it, not recompute it.
