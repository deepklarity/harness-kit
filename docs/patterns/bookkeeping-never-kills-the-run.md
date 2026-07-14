tags: robustness, paths, retry, bookkeeping, error-handling

# Bookkeeping Never Kills the Run

Support code — cost tracking, comment posting, telemetry, trace ingestion — must never be able to crash the primary work it observes. Validate its inputs at construction time (absolute paths, reachable endpoints), retry its transient failures, and downgrade everything else to a warning.

## Why it matters

Three separate multi-minute agent runs died to bookkeeping in one week: a cost-store write, a dropped HTTP connection, a reviewer boot. In each case the *product work had already succeeded* and was destroyed or mislabeled by the accounting around it. The asymmetry is total: a lost cost row costs a number; a killed 30-minute run costs the run, the diagnosis time, and user trust.

Corollary on paths: **relative paths are time bombs**. Anything constructed relative to cwd breaks the moment cwd changes or vanishes mid-process — resolve to absolute at construction, not at use.

## The signal

You're writing code whose job is to *record* something about the main work (costs, comments, traces, metrics), and its failure path is unhandled — or it stores a path/URL it will dereference later, verbatim as received. Ask: "if this line throws at minute 29 of a 30-minute run, what dies?" If the answer is "the run", wrap it.

## Instances

- **2026-07-05 / F33 CostStore relative path**: `CostStore` held `.odin/costs` relative; a retry changed cwd mid-run, the write raised `FileNotFoundError`, and a healthy 30-minute run crashed. Fix (9cf762a): absolute path at construction + bookkeeping exceptions can never propagate into the run.
- **2026-07-05 / F36 no transport retry**: One dropped Django dev-server connection (`httpx.RemoteProtocolError`) crashed a 9-minute healthy run because the TaskIt client had zero retry for transient transport failures. Fix (c8f37b0): retry transient errors.
- **2026-07-05 / F37 relative trace path**: Reflection passed a relative path that the microVM parsed as a named volume — reviewer never booted. Same relative-path root as F33, one subsystem over. (Verdict-coercion half of F37: see docs/patterns/no-fabricated-verdicts-from-infra-failures.md.)
