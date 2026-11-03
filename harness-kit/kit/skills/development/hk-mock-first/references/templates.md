# Workspace file templates

One heading per file in `docs/mock-first/<feature-slug>/`. Copy the block, fill the brackets.

## tracker

`tracker.md`, the resumption anchor. Read first in every session, updated last.

```markdown
# Mock-first: [feature name]

## Feature
What we're building: [one line]
Interfaces affected: [pages, components, CLI commands]

## Current state
Phase: [exploring | mocking | accepting | deepening-layer-N | shipped]
Current layer: [surface | layer-N]

## Layer progression
- surface: [status], [what was mocked, acceptance verdict]
- layer-1: [status], [what was deepened, test results]

## Decisions
- [Decision and why: what the user accepted, rejected, or changed]

## What we've learned
- [Edge cases discovered, states that were missing]

## Next
[What to do next, or "shipped"]
```

## states

`surface/states.md`, written in Phase 0.

```markdown
# Interface states for [feature]

## [Interface or component]
- Happy path: [description]
- Empty: [no data yet, what does the user see?]
- Loading: [data is being fetched]
- Error: [API failed, validation failed, permission denied]
- Partial: [some data loaded, some pending]
- Transitions: [optimistic updates, confirmation dialogs]
- Edge cases: [long text, many items, zero items, special characters]
```

## acceptance

`surface/acceptance.md`, written in Phase 2.

```markdown
# Acceptance: [feature name]

Verdict: [accepted, accepted with changes, or rejected], and in one sentence why.

## What works
- [What the user approved]

## What needs to change
- [Change requested, with priority]

## Missing states or scenarios
- [Things not in the original states catalogue]

## How to run the mocked version
[Exact commands or steps]
```

## boundary

`layer-N/boundary.md`, step 1 of each layer.

```markdown
# Layer N: [name]

## What's being replaced
[Which mocks are swapped for real code]

## New complexity
[What can now go wrong that could not before]

## Files affected
[List]

## What could break
[Blast radius if this layer is wrong]
```

## test-plan

`layer-N/test-plan.md`, step 2 of each layer. The test-writing subagent sees this file and `surface/acceptance.md`, nothing else.

```markdown
# Test plan: layer N

## Tests to write
- [ ] [Behaviour: what it proves]
- [ ] [Edge case: what it proves]
- [ ] [Error case: what it proves]

## What these tests replace
[Which mock behaviours are now tested for real]
```

## verification

`layer-N/verification.md`, step 5 of each layer.

```markdown
# Verification: layer N

## Tests
[The scripts/verify.sh result for the suites touched]

## Interface check
[Does the experience still match surface/acceptance.md? Note any change, even an improvement.]

## Mock boundary moved to
[Where the mocks now sit, the boundary for the next layer]
```

## summary

`summary.md`, written at ship or when pausing long-term. Self-contained: a reader in three months should not need the layer folders.

```markdown
# Summary: [feature name]

## Result
Layers deepened: [N]
Iterations on mocks: [count]
[The scripts/verify.sh summary table]

## Architecture decisions
- [Decision made during deepening, and why]

## What the mocks caught early
- [Problems found in the mock phase that would have cost more later]

## Layer progression
- Surface: [what was mocked, what it showed]
- Layer 1: [what was deepened, surprises]

## If revisiting later
[Why things are built this way]
```
