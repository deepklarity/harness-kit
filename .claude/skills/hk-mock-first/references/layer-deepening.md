# Layer Deepening Reference

How to identify legitimate complexity boundaries for the mock-first approach. A "layer" isn't a function call or abstraction level — it's a boundary where the *nature* of the problems changes.

## The Test: Is This a Real Layer Boundary?

A boundary is a real layer if crossing it introduces **new categories of failure** that didn't exist before. If the only new thing is "more code," it's not a separate layer — it's part of the current one.

| Signal | Real layer boundary | Not a layer boundary |
|--------|-------------------|---------------------|
| New failure modes | Network errors, auth failures, race conditions | More of the same type of logic |
| Different expertise | "Now I need to think about SQL" vs "Now I need to think about React" | Same kind of thinking, just more of it |
| Different test strategy | Unit tests → integration tests → E2E tests | More unit tests of the same kind |
| Different rollback cost | "Undo this migration" is harder than "revert this component" | Same effort to undo |
| External dependency | Crosses a process/service/network boundary | Stays within the same process |

## Common Layer Patterns

### Full-Stack Web Feature (React + Django)

```
Surface:     React components with mock data (JSON fixtures, mock hooks)
             → Problems: UX, layout, interaction design, state display
             → Mock boundary: custom hooks or data-fetching layer

Layer 1:     Real state management (React Query/context/Redux)
             → Problems: cache invalidation, optimistic updates, stale data, re-renders
             → Mock boundary: API client functions

Layer 2:     Real API calls to Django endpoints
             → Problems: auth tokens, CORS, pagination, error responses, loading races
             → Mock boundary: Django view → serializer (return mock queryset)

Layer 3:     Real serializers + business logic
             → Problems: validation rules, permission checks, field computation, N+1 queries
             → Mock boundary: model/ORM layer (use factory_boy or fixtures)

Layer 4:     Real database operations
             → Problems: migrations, constraints, indexes, transactions, data integrity
             → No more mocks — real persistence
```

### CLI Feature (Python CLI like Odin)

```
Surface:     Output formatting with mock data (rich tables, progress bars)
             → Problems: terminal width, color support, long text truncation
             → Mock boundary: the function that produces the data

Layer 1:     Real command parsing + orchestration logic
             → Problems: argument validation, subcommand routing, config loading
             → Mock boundary: external service calls

Layer 2:     Real service integration (API calls, file I/O, subprocess)
             → Problems: timeouts, auth, file permissions, process failures
             → Mock boundary: the external systems themselves

Layer 3:     Real external systems
             → Problems: rate limits, API versioning, network partitions
             → No more mocks
```

### API Endpoint (Django REST)

```
Surface:     API response shape with mock data (hardcoded JSON in view)
             → Problems: response structure, field naming, pagination format
             → Mock boundary: serializer → queryset

Layer 1:     Real serializer + model relationships
             → Problems: nested serialization, field computation, N+1
             → Mock boundary: queryset (use fixtures)

Layer 2:     Real queryset + business logic
             → Problems: filtering, ordering, permissions, edge case data
             → Mock boundary: external service calls

Layer 3:     Real external integrations
             → Problems: third-party API contracts, webhook handling
             → No more mocks
```

### Multi-Agent Orchestration Feature (Odin-style)

```
Surface:     Task board display / spec output with mock task data
             → Problems: status rendering, DAG visualization, progress reporting
             → Mock boundary: orchestrator output

Layer 1:     Real orchestration logic (wave planning, dependency resolution)
             → Problems: cycle detection, wave ordering, failure propagation
             → Mock boundary: harness execution (mock the agent calls)

Layer 2:     Real harness execution with mock agent responses
             → Problems: timeout handling, retry logic, cost tracking, output parsing
             → Mock boundary: the LLM API itself

Layer 3:     Real LLM calls
             → Problems: token limits, rate limits, model behavior variance, cost
             → No more mocks
```

## How Deep to Go

Not every feature needs to reach the deepest layer in this skill's workflow. The decision depends on:

1. **Is this a UI-only change?** Surface + Layer 1 might be enough. The API already exists and works.
2. **Is this a new end-to-end feature?** You'll likely go 3-4 layers deep.
3. **Is the user exploring an idea?** Surface mock might be all they need to decide if it's worth building.

The user's acceptance at each gate determines whether to go deeper. Some features get accepted at the mock stage and stay as "ready to implement later."

## TDD at Each Layer

The test strategy changes with the layer:

| Layer | Test type | What you're proving |
|-------|-----------|-------------------|
| Surface | Component/snapshot tests | The interface renders correctly with various data shapes |
| Layer 1 | Unit tests for state logic | State transitions, derived values, cache behavior |
| Layer 2 | Integration tests (mocked network) | API contract compliance, error handling, auth flow |
| Layer 3 | Integration tests (real services, test DB) | Business rules, data integrity, permission logic |
| Layer 4+ | E2E / live verification | The whole thing works together in reality |

Each layer's tests should:
1. **Fail first** — written before implementation
2. **Test the new complexity** — not re-test what the previous layer already proved
3. **Use the previous layer's mocks as fixtures** — the mock data from the surface phase becomes test data for deeper layers
