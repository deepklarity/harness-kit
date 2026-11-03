# Layer boundaries and stack patterns

## Is this a real layer boundary?

A boundary is a real layer if crossing it introduces **new categories of failure** that didn't exist before. If the only new thing is "more code," it's not a separate layer — it's part of the current one.

| Signal | Real layer boundary | Not a layer boundary |
|--------|-------------------|---------------------|
| New failure modes | Network errors, auth failures, race conditions | More of the same type of logic |
| Different expertise | "Now I need to think about SQL" vs "Now I need to think about React" | Same kind of thinking, just more of it |
| Different test strategy | Unit tests → integration tests → E2E tests | More unit tests of the same kind |
| Different rollback cost | "Undo this migration" is harder than "revert this component" | Same effort to undo |
| External dependency | Crosses a process/service/network boundary | Stays within the same process |

## Stack patterns

### Full-stack web feature (React + Django)

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
             → Mock boundary: model/ORM layer (use fixtures)

Layer 4:     Real database operations
             → Problems: migrations, constraints, indexes, transactions, data integrity
             → No more mocks — real persistence
```

### CLI feature (Python)

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

### API endpoint (Django REST)

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

### Async background processing (queue + external service)

```
Surface:     Submit UI / status display with mock results
             → Problems: progress indication, status polling, error display
             → Mock boundary: API response layer

Layer 1:     Real API + queue dispatch
             → Problems: task queuing, status transitions, retry logic, timeout handling
             → Mock boundary: extraction pipeline (mock the processing results)

Layer 2:     Real processing pipeline with mock external-service responses
             → Problems: input parsing, format conversion, structured output
             → Mock boundary: the external service itself

Layer 3:     Real LLM calls
             → Problems: token limits, rate limits, model behavior variance, cost
             → No more mocks
```
