tags: fallback, error-handling, verdict, fail-loud, reflection

# Never Fabricate a Verdict from an Infra Failure

When infrastructure fails, report an infrastructure failure — never coerce it into a domain answer. A fallback that invents a plausible-looking verdict ("NEEDS_WORK", "unhealthy", "0 results") from a crash, an empty output, or a proxy check is strictly worse than no fallback, because everything downstream acts on a lie.

## Why it matters

The F37 incident: a sandboxed reviewer never booted (relative trace path parsed as a named volume), produced empty output, and the parser's "never leave the verdict empty" fallback coerced that into `NEEDS_WORK`. The fake verdict drove a rework loop and ultimately marked a *completed, committed* task FAILED — "*task 103 failed and this is impossible to debug like this*". The fix (commit e6de0fa): reviewer failures surface as reviewer failures and never drive rework. Same shape, different subsystem: health probes that checked CLI version instead of live auth (F12) reported providers healthy while GLM/MiniMax hung silently for 12 minutes unauthenticated.

## The signal

You're writing a `default=`, `except: return <domain value>`, or "never leave X empty" branch — and the value you're about to return is indistinguishable from a genuinely computed result. If a caller can't tell "the check ran and said no" from "the check never ran", split those into different states before shipping.

## Instances

- **2026-07-05 / F37 reflection verdict coercion**: Relative path → microVM mount error → reviewer never booted → empty output → parser fabricated `NEEDS_WORK` → bogus rework loop → healthy task marked FAILED. Fixed with path absolutization + reviewer-failure verdicts that cannot trigger rework, with tests for the infra-failure path (the regression had escaped the suite precisely because only happy-path parsing was tested).
- **2026-07-03 / F12 proxy health checks**: Provider probe verified the CLI binary/version, not auth or tier validity — a false "healthy" that let unauthenticated GLM/MiniMax dispatches burn full timeouts with zero output. Probe now needs a minimal live-auth call: verify the dependency you actually rely on, not a proxy for it.
