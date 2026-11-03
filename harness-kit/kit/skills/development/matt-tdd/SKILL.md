---
name: matt-tdd
description: "Write tests that survive refactors, test-first. Triggers on: 'write tests', 'add tests', 'test this module', 'test-first', 'red-green', 'integration tests', or /matt-tdd."
imported: true
imported_from: https://github.com/mattpocock/skills
preset_enabled: false
---

# Test-driven development

Tests go red before the code goes green. This skill is the reference that makes the red → green loop produce tests worth keeping: where tests go, what a good one looks like, and how the loop runs inside this repo's wave rule.

## Steps

1. **Name the seams.** A seam is the public boundary you test at: the interface a caller uses, where you can observe behaviour without reaching inside. Write the seams under test as the first lines of the scenario matrix (CLAUDE.md, Wave 0), then the happy paths, edge cases and failure modes. Ask the user only when two candidate seams would produce different tests and the spec does not settle it. When the shape of the interface is itself in question, call the Skill tool with `matt-codebase-design` for the module, seam and depth vocabulary.
2. **Write the tests red.** A haiku explorer lists the area's test layout, fixtures and naming. Dispatch a sonnet subagent with the scenario matrix, the seams, that list, `tests.md` and `mocking.md`, and nothing about the implementation (CLAUDE.md, Wave 1); it does not see this file. It writes one test per matrix line, named for what the caller gets, in the project's own words.
3. **Confirm red.** Run the verify gate named in CLAUDE.md for the area touched. Every new test fails. A test that passes now is checking nothing; fix or delete it before going on.
4. **Go green.** Dispatch the implementation subagent with the failing tests and the requirements (CLAUDE.md, Wave 2). Write only enough code to pass the tests on disk; a test that turns out wrong is fixed at the seam, not worked around in the code.
5. **Confirm green, refactor, verify live.** The verify gate named in CLAUDE.md for the suites touched passes. Refactor is its own pass after green: simplify the code the tests cover, run the suite again. Then the behaviour is seen in the running system (UI, API response, CLI output; CLAUDE.md, Wave 3) and that check is written in the final message. Done means green twice and seen live.

## Reference

Tests verify behaviour through the seam; the code inside can change entirely and the test still passes. `tests.md` shows good and bad tests side by side. `mocking.md` says what to mock (system boundaries only) and how to design a boundary so it mocks cleanly.

Two ways a test stops being worth keeping:

- **Implementation-coupled**: it mocks an internal collaborator, calls a private method, asserts on call order, or verifies through a side channel (querying the database instead of using the interface). The tell: the test breaks on a refactor when behaviour has not changed.
- **Tautological**: the expected value is computed the way the code computes it (`expect(add(a, b)).toBe(a + b)`, a snapshot derived by hand the same way, a constant asserted equal to itself), so it passes by construction. Expected values come from an independent source: a known-good literal, a worked example, the spec.

Each test names one matrix behaviour. A test that checks a field exists or a function was called, instead of what a caller sees, is a shape test and belongs in neither wave.

## Example

- Verify gate by area: the verify gate with the suite name.
- Full verify gate: the verify gate with no argument.
