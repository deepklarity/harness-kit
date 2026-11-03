---
name: hk-mock-first
description: "Mock a feature's user-facing surface with realistic data, get the user to accept it, then replace the mocks one layer at a time. Triggers on: 'mock first', 'let me see it first', 'prototype this', 'simulate this feature', 'build this layer by layer', or /hk-mock-first."
argument-hint: "[feature description or area to mock]"
allowed-tools: Bash, Read, Edit, Write, Agent, Grep, Glob
---

# Mock-first, layer-by-layer feature development

Build the user-facing experience on mock data first, get the user to accept it, then swap mocks for real code one complexity layer at a time. Experience problems surface before any plumbing exists, when they are cheap to fix.

Skip this skill for a bug fix, a backend change with no interface, a config or dependency change, or a feature whose interface the user has already seen and accepted: a bug fix goes to `hk-rca`; the rest go straight to `matt-tdd`.

## Context

<feature_context> $ARGUMENTS </feature_context>

If the context is empty or unclear, ask what feature is being built and which user-facing interfaces it touches (pages, CLI output, API responses that drive UI). The workspace is `docs/mock-first/<feature-slug>/`.

## The workspace

Everything lives on disk; the workspace is the record of what was mocked, what was accepted and what has been deepened, and the place a new session resumes from. Read `tracker.md` first in every session and at every phase change; update it last before stopping.

```
docs/mock-first/<feature-slug>/
├── tracker.md                  # Live state (the resumption anchor)
├── surface/
│   ├── interface-map.md        # All user-facing interfaces this feature touches
│   ├── mock-data/              # Mock data files (JSON, fixtures, factories)
│   ├── mock-components/        # Mock UI components, stubs, or test pages (if any)
│   ├── states.md               # Every interface state catalogued
│   └── acceptance.md           # The user's acceptance notes
├── layer-1/
│   ├── boundary.md             # What this layer replaces and what it introduces
│   ├── test-plan.md            # The tests, written before implementation
│   ├── changes.md              # What changed (files, diffs)
│   └── verification.md         # Tests pass and the interface still matches
├── layer-2/ ...
└── summary.md                  # Written when the feature ships or pauses long-term
```

Every file above has a template in `references/templates.md`, one heading per file.

## Phase 0: explore and map

1. **Map the interaction surface.** Dispatch a haiku explorer for the points where a person's eyes and hands meet the system, skipping services, utilities and middleware:

   ```
   Agent(model: haiku, subagent_type: Explore)

   Map all user-facing interfaces that would change for: [feature description]
   For each: file path and component or page name; what the user sees and does there now;
   what changes with the feature; what data it needs.
   ```

2. **Catalogue the states.** Write `surface/states.md` (template: `references/templates.md#states`): for every interface, the happy path, empty, loading, error, partial and transition states, and the edge cases (long text, many items, zero items). Without the catalogue the mock is a happy path and the empty, error and loading states turn up during real implementation.
3. **Write `surface/interface-map.md`.** Everything built later traces back to something on this map.
4. **Write `tracker.md`** (template: `references/templates.md#tracker`) with the phase set to `mocking`.

## Phase 1: mock at the surface

Build the feature's user-facing experience on mock data only. Done when the user can see, click and evaluate the whole feature with no backend, service or persistence change.

Mock data exercises the states catalogue: long names, missing optional fields, every status combination, timestamps at boundaries. A list gets 50 items, a form gets validation errors, a workflow gets every transition. The mock boundary sits where data enters the interface (the API call, the data-fetching hook, a data provider) so the interface code stays untouched when real data arrives.

Write the component tests red first, one per row of `surface/states.md`, using Phase 3 steps 3 and 4 with `states.md` as the plan; the mock data is the fixture.

- **Frontend**: mock data in `surface/mock-data/`; mock at the data-fetching layer the project uses (React Query, custom hooks); real components from the project's library; a mock route or feature flag to toggle mock mode.
- **CLI**: output fixtures in `surface/mock-data/`; the formatting and display logic runs on them; the user can run the command and see realistic output.
- **API-driven**: response fixtures wired into the consumer at the fetch boundary.

Write how to run the mocked version into `surface/acceptance.md` so another session can start it without the backend.

## Phase 2: acceptance gate

Show the user the mocked feature and walk the states catalogue with them: does the happy path feel right, does each state make sense, what is missing, what is wrong (layout, flow, hierarchy, wording). Record the answers in `surface/acceptance.md` (template: `references/templates.md#acceptance`).

Iterate on the mocks until the verdict is accepted. Phase 3 starts only after acceptance, because deepening an unaccepted feature spends real implementation on something that will change.

## Phase 3: deepen, one layer at a time

A layer is a boundary where the nature of the problems changes; `references/layer-deepening.md` has the boundary test and the stack patterns. The usual progression:

| Layer | What you're replacing | New complexity introduced |
|-------|----------------------|--------------------------|
| Surface | Nothing, mocks only | UX, layout, interaction design |
| Layer 1 | Mock data → real state management | State transitions, optimistic updates, caching |
| Layer 2 | Mock API → real API calls | Network errors, auth, pagination, rate limits |
| Layer 3 | Mock service → real business logic | Validation rules, edge cases, domain invariants |
| Layer 4 | Mock persistence → real DB | Migrations, queries, transactions, data integrity |
| Layer 5 | Mock externals → real external services | Timeouts, retries, API changes, cost |

Most features have two or three of these. For each layer, in order:

1. **Document the boundary** in `layer-N/boundary.md` (template: `references/templates.md#boundary`): what is replaced, what can now go wrong, the files that change.
2. **Write the test plan** in `layer-N/test-plan.md` (template: `references/templates.md#test-plan`): one line per behaviour, edge case and error case, and which mock behaviours they replace. This is the scenario matrix.
3. **Write the tests red.** Have a haiku explorer list the area's test layout and naming. Dispatch a sonnet subagent that receives only `test-plan.md`, `surface/acceptance.md`, that list, and matt-tdd's `tests.md` and `mocking.md` (`docs/skills/development/matt-tdd/`); it writes the test files and returns. It does not see `boundary.md` or any skill's step list. Run the verify gate named in CLAUDE.md for the suite the change touched; every new test fails before step 4. A test that passes now is checking nothing; fix or delete it.
4. **Implement.** Dispatch the implementation subagent with the failing tests, `boundary.md` and the accepted behaviour. It replaces mocks at this layer only; the next layer's mocks stay. Log the files and diffs in `layer-N/changes.md`.
5. **Verify** in `layer-N/verification.md` (template: `references/templates.md#verification`): the suite passes, the interface still matches what was accepted in Phase 2, and where the mock boundary now sits.
6. **Update `tracker.md`**, then show the user anything they can now see differently (real data replacing mock data counts) and record whether it still meets acceptance.

## Phase 4: ship

1. Run the full verify gate named in CLAUDE.md.
2. Do a final live check with the user.
3. Write `summary.md` (template: `references/templates.md#summary`) with the verify table in its Result section, and set `tracker.md` to `shipped`.
4. Append any learning worth keeping to the matching file in `docs/patterns/` (append, never fork).

Mock data stays in `surface/mock-data/` after shipping: it documents the intended behaviour and serves as test fixtures for deeper layers.

## Example

- Scoped suite: the verify gate with a suite argument.
- Full gate: the verify gate with no argument.
