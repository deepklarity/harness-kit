# Testing and proof: the operational checklist

The short form of `testcase_process_and_philosophy.md`, kept next to it so `CLAUDE.md` can stay short.

**Tests pass ≠ feature works.** Static tests validate frozen assumptions. Only live verification proves the user can see and use the feature. Every change must close the full loop: **test → fix → verify live**.

The full testing framework, RCA protocol, stale test handling, and quality standards are in **`docs/testing_process/testcase_process_and_philosophy.md`**. What follows is the operational checklist.

### The workflow: Think → Test → Code → Verify

**This ordering is enforced structurally, not by instruction.** See "Test-First Wave Execution" in the subagent section. The test-writing step runs as a separate subagent that has no implementation context, making it impossible to write implementation first. Do not combine test-writing and implementation into the same subagent dispatch.

1. **Scenario matrix** (mandatory, visible in conversation) — happy paths, edge cases, failure modes, integration seams. Not optional. The matrix prevents writing tests that confirm what you're about to implement rather than challenging whether it's correct.

2. **Write failing tests first** (separate subagent, no impl context) — each scenario maps to a test. The test-writing subagent receives ONLY behavioral requirements and existing test patterns. Run the tests. They must fail. A test that passes before implementation is a false positive. **Gate: do not proceed until failures are confirmed.**

3. **Write clean code scoped to the requirements** (separate subagent, receives failing tests) — implementation subagents receive the failing test files so they know what to satisfy. The solution should be properly thought through — no duct-tape patches, no hardcoded returns, no workarounds that defer real work to the next person. But don't add capabilities, abstractions, or extension points beyond what the requirements demand.

4. **Verify live** — after static tests pass, confirm the behavior in the real system (UI, API, spec run). This step is not optional.

### Bug fix workflow: Reproduce → Locate → RCA → Test → Fix → Verify

When something is reported broken:

1. **Reproduce** — can you see the bug? What's the exact input?
2. **Locate** — run diagnostic scripts (`--brief` for quick check), then check API, then check frontend
3. **RCA** — ask "why?" until you reach the root cause (not the symptom)
4. **Write the failing test** — reproduces the exact bug, fails now, passes after fix
5. **Fix and verify** — run full suite + live verification
6. **Document** — root cause and prevention in commit message

See `docs/testing_process/testcase_process_and_philosophy.md` § "When Something Breaks: RCA Protocol" for the full protocol.

### Anti-patterns (non-negotiable)

- **Testing your mocks** — if mock returns X and you assert X, you tested unittest.mock
- **String-presence only** — `assert "cost" in response` proves concatenation, not correctness
- **Happy-path only** — if every test uses the simplest input, the suite gives false confidence
- **Skipping live verification** — the #1 cause of "tests pass but feature is broken"

### Test types and when to run each

| Change type | Run these | Also verify |
|-------------|-----------|-------------|
| Logic/algorithm | `odin/tests/unit/` | — |
| Harness/pipeline | `odin/tests/mock/` + snapshot tests | Live spec run |
| Model/serializer | `taskit-backend/tests/` + snapshot tests | API response check |
| Frontend component | `taskit-frontend/ npm run test:run` | Visual check in browser |
| Model field added | Snapshot tests + serializer test | API response includes field + UI renders it |

### One-shot verify gate

The single source of truth for "is the repo green?" is `scripts/verify.sh`. POSIX sh,
runnable from repo root. No args runs every suite; pass suite names to scope it.

```bash
scripts/verify.sh                    # all four suites, summary table, wall clock
scripts/verify.sh odin snapshots     # subset only
```

Internally it shells out to the per-suite commands in the rows above (with the
right working directory and env vars), captures each tool's native summary
line, prints a SUITE / RESULT / TESTS / SECONDS table, and exits non-zero if
**any** suite is red — including the 5 known-red snapshot cost tests (a
separate task fixes them; the gate reports them honestly rather than masking
or xfailing). Per-suite output is preserved under `.verify-logs/`.

### Snapshot tests

Capture a known-good spec execution as JSON, validate structural invariants (not exact values). Bridge between fast unit tests and slow live runs.

```bash
# Capture after a spec run
cd taskit/taskit-backend && python testing_tools/snapshot_extractor.py <spec_id> ../../tests/e2e_snapshots/<name>

# Run
python -m pytest tests/e2e_snapshots/ -v
```

When to re-capture: after model/serializer changes, harness changes, or when snapshot age > latest migration. See `docs/testing_process/testing_end_to_end.md` for the full snapshot workflow.

### Documentation & follow-through

- Update relevant `CLAUDE.md`, `TEST_PLAN.md`, `README.md` when changing behavior
- Document new env variables in `CLAUDE.md` and `.env.example`
- End with `Next Steps:` — concrete commands to run or things to verify
