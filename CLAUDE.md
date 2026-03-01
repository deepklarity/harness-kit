# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Memory Policy

**Never use MEMORY.md files.** Do not create, read, update, or reference any `MEMORY.md` files in this repository. All persistent project context belongs in `CLAUDE.md` files (root and per-project). If you need to store learnings, use `/workflows:compound` to write to `docs/solutions/` instead.

## Git Safety: No Stash

**Never run ANY `git stash` command** — not `git stash`, `git stash list`, `git stash pop`, `git stash show`, or any other stash subcommand. This is a hard ban on the entire `git stash` family, including read-only variants. Multiple agents may work in parallel on the same repo; stash operations create invisible shared state that causes silent data loss across concurrent sessions. Use branches, temporary commits, or disk-anchored files instead. If you need to check for uncommitted changes, use `git status` or `git diff`.

## Design Principles

**Default First**: Every feature should work out of the box with sensible defaults. Configuration, agent assignments, and settings should be *suggestive* — provide a good default that the user can override, rather than requiring upfront configuration. The system picks reasonable choices automatically; the user intervenes only when they want something different.

This applies everywhere:
- **Odin agent assignments**: Cheapest capable agent is suggested automatically; user can reassign before execution
- **Configuration**: Built-in defaults → project-local config → explicit flags (each layer overrides the previous)
- **Workflow**: `odin run` works end-to-end with defaults; staged commands (`plan`/`exec`/`assemble`) let users intervene when needed

## Philosophy Grounding

**Read `odin/docs/philosophy.md` at the start of every session.** It defines the 19 core tenets that govern how this repo is built. Don't just read it — internalize it. Every design decision, code review, and architectural choice should be traceable to one or more of those tenets.

### Applying philosophy in practice

The tenets are not aspirational — they're operational filters. Use them actively:

- **Before writing code**: Ask "which tenets does this serve?" If you can't name one, question whether the work is necessary.
- **During code review**: Check for tenet violations. Code that works but violates a tenet (e.g., hardcoded pipeline stages violating *First Principles*, or AI-smelling output violating *No Slop*) is not done.
- **When making trade-offs**: The tenets resolve ambiguity. "Should I add a config option or pick a default?" → *Default First* + *Good Enough* say pick the default. "Should I build a custom solution or compose existing primitives?" → *First Principles* + *Modular Composition* say compose.
- **When designing features**: Run the design through the relevant tenets as a checklist. A new execution feature should satisfy *Determinism*, *Proof of Work*, *Async Heavy*, and *Pareto Observability* at minimum.

### Key tenets to keep top of mind

These are the tenets most frequently relevant to day-to-day work:

| Tenet | One-liner | Watch for violations |
|---|---|---|
| **No Slop** (#12) | AI output meets the same bar as human code | Boilerplate comments, filler docstrings, "AI smell" |
| **Default First** (#8 Good Enough + design principle) | Work out of the box, override when needed | Requiring config before anything runs |
| **First Principles** (#11) | No hardcoded stages — everything is a task | Special-casing what should be composable |
| **Proof of Work** (#4) | Every task has evidence | Changes without verification, "it should work" |
| **Pareto Delegation** (#2) | Cheapest capable agent | Using opus for haiku-tier work |
| **Human-First Legibility** (#13) | Optimize for human scanning | Dense JSON in user-facing output |
| **No Slop** + **Taste** (#12, #17) | Volume is easy; judgment is the filter | Accepting first-draft AI output without curation |

### Philosophy violations as code smells

Treat tenet violations the same way you treat code smells — they signal something is wrong even if the code technically works. When you spot one, flag it explicitly: "This violates tenet X because Y" and propose the fix. Don't ship code that violates the philosophy just because tests pass.

## Methodical Problem-Solving

**Think step by step. Observe before acting. Change one thing at a time.**

### Breadcrumb-First Exploration

**Before exploring the codebase for how a flow works, check `docs/breadcrumb_analysis/_INDEX.md` first.** It contains end-to-end workflow traces with FLOW.md (high-level), DETAILS.md (file/function level), and DEBUG.md (logs, search patterns, commands). The index maps common symptoms directly to the right debug doc. If a breadcrumb covers your area, read it before spawning any exploration subagents — it's cheaper and more accurate than re-discovering the same information.

When something is broken, unexpected, or unclear — resist the urge to jump to a fix. Follow this discipline:

1. **Observe first** — Read the actual error message/traceback carefully. Run a diagnostic script (`--brief` first, then `--full` if needed). Check what IS happening before theorizing about what SHOULD happen.
2. **State your hypothesis visibly** — Before making any change, write out: "I think the problem is X because Y, and changing Z should fix it because W." This makes reasoning checkable and prevents shotgun debugging.
3. **Verify assumptions before building on them** — Don't assume a field exists, a contract holds, or a function returns what you expect. Check it. `--json --sections basic` is cheap. Reading the wrong assumption forward through 3 files of changes is expensive.
4. **Change one thing at a time** — If you change 3 things and it works, you don't know what mattered. If it doesn't, you've added complexity. Isolate the variable.
5. **Trace the data flow** — Follow data from source to symptom. The bug is at the point where actual diverges from expected. Don't guess where that is — trace to it.
6. **When stuck, zoom out** — If a fix isn't working after 2 attempts, stop and reconsider the hypothesis. The most common reason a fix fails is that the diagnosis was wrong, not that the fix was wrong.

**Anti-patterns**: Changing code and running tests hoping they pass. Fixing a symptom without understanding the cause. Reading 10 files "for context" instead of running the diagnostic script that shows the problem directly. Making a fix, seeing a new error, fixing that, seeing another — this cascade means the first fix was wrong.

## Disk-Anchored Development

**The disk is the memory, not the conversation.** Conversations compact, sessions end, context windows fill. Any work that spans more than a single focused task should leave a trail on disk that a future session can pick up without re-reading the full conversation history.

### When to anchor on disk

- **Multi-step features**: Use `docs/mock-first/<feature-slug>/` with a `tracker.md` (see `/dk-local-mock-first-approach`)
- **Iterative refinement**: Use a scratch directory with `loop.md` (see `/dk-close-the-loop`)
- **Debugging investigations**: Write hypotheses and findings to `docs/solutions/` as you go, not just at the end
- **Any work that might be continued in a new session**: If you'd need to re-explain context to resume, it belongs on disk

### Organization rules (preventing workspace clutter)

| Content type | Location | Lifecycle |
|---|---|---|
| Feature mock workspaces | `docs/mock-first/<feature-slug>/` | Kept until feature ships, then `summary.md` stays |
| Iteration scratch dirs | Sibling to the thing being refined | Cleaned up after loop closes (summary stays) |
| Compounded learnings | `docs/solutions/<category>/` | Permanent |
| Loop audit reports | `docs/loop_audits/` | Permanent |
| Breadcrumb analyses | `docs/breadcrumb_analysis/` | Permanent |
| Temp debugging output | `/tmp/` or gitignored dirs | Ephemeral — never commit |

**The tracker pattern**: Any multi-session workflow should have a single `tracker.md` (or `loop.md`) file that answers: *Where are we? What was decided? What's next?* This file is the first thing to read when resuming and the last thing to update before stopping.

**Cleanup discipline**: When a workflow completes, write its `summary.md`, then clean up intermediate artifacts that aren't useful as documentation. The summary should be self-contained — someone reading it 3 months later shouldn't need the intermediate files to understand what happened and why.

## Subagent Strategy: Parallel by Default

**The main conversation is a coordinator, not a worker.** Every file read, search, or code exploration done directly in the main conversation burns expensive opus tokens. Default to delegating data-gathering and mechanical work to subagents, and reserve the main context window for synthesis, judgment, and decisions.

**Parallel is the default, sequential is the exception.** Whenever you have 2+ independent subagent tasks, launch them in a single message with multiple Task tool calls. Do not wait for one subagent's result before launching another unless the second genuinely depends on the first's output. Sequential subagent dispatches for independent work is a performance anti-pattern — it turns a 30-second parallel fan-out into a 3-minute serial chain.

**The rule of thumb**: If you're about to spawn a subagent, ask "is there another subagent I should spawn at the same time?" If yes, batch them into one message. This applies to planning, exploration, implementation, and verification phases equally.

**Non-negotiable model assignments (NEVER override):**
- **Plan subagents → always opus.** NEVER use sonnet or haiku for `subagent_type: Plan`. Planning, architectural analysis, and trade-off evaluation require the strongest model. This is a hard constraint, not a suggestion. When using EnterPlanMode or spawning a Plan agent via the Task tool, always set `model: "opus"` explicitly.
- **Explore / read / search subagents → always haiku.** File searches, glob/grep, reading files, summarizing structure — always haiku. No exceptions.

When spawning subagents via the Task tool, choose the cheapest capable model:

- **Haiku** — Exploration and mechanical tasks: file searches, grep/glob lookups, reading files and summarizing their structure/exports, checking what conventions a directory follows, finding all usages of a function, formatting checks, running a single well-defined command, creating a file from a clear template, adding imports. Use when the task has a discoverable answer or a clear specification.
- **Sonnet** — Judgment-requiring tasks: writing code that must follow existing patterns, multi-file refactoring with clear targets, writing tests for existing code, code reviews, researching a focused question that requires cross-referencing several files. Use when the task needs reasoning but the patterns are established.
- **Opus** — High-complexity tasks: architectural decisions, debugging subtle cross-system issues, designing new abstractions, writing code that requires understanding multiple interacting systems, ambiguous requirements that need creative problem-solving.

**Escalation protocol** (applies to sonnet/haiku decisions only — Plan is always opus, Explore is always haiku): Start with the cheapest model that can plausibly handle the task. If a haiku agent returns a shallow or incomplete result, re-run with sonnet — don't pre-emptively use a heavier model "just in case." If sonnet misses nuance, escalate to opus. Most exploration is haiku-tier. Most implementation is sonnet-tier. Reserve opus for tasks where getting it wrong would cost more than the model price difference.

### When to delegate vs. do directly

**Delegate to subagents** (the default for most work):
- "What files handle X?" / "How is Y structured?" / "What pattern does Z follow?" → haiku explorer
- "Read these 3 files and summarize the interface contract" → haiku
- "Find all places where function X is called and list the call signatures" → haiku
- "Write this test / create this file / add this import" (clear spec, no ambiguity) → haiku
- "Write this component following the pattern in ComponentX" → sonnet
- "Refactor module X to extract Y" → sonnet

**Do directly in main conversation** (when you need the content for an imminent decision):
- You're about to make an architectural choice and need to see the code yourself
- The answer will immediately feed into a judgment call in the next sentence
- You've already delegated and need to verify/synthesize subagent results
- A quick single-file read (< 100 lines) that's faster than spawning a subagent

**Anti-patterns**:
- Reading 5+ files sequentially in the main conversation to "understand the codebase" before writing code. Instead, spawn parallel haiku explorers for each area, synthesize their summaries, then make decisions.
- Spawning one subagent, waiting for its result, then spawning the next independent subagent. If the tasks are independent, they go in one message.
- Running backend tests, waiting, then running frontend tests. Independent test suites run in parallel.

### Plan mode — Parallel exploration, then synthesize

**Judgment stays in the main conversation; data gathering is delegated.** The main opus conversation owns all architectural thinking, trade-off analysis, and plan authorship. But factual research — finding files, reading code structure, checking conventions, discovering what exists — should be delegated to subagents to conserve the main context window.

**Planning follows a fan-out → synthesize → decide pattern:**

1. **Fan-out (one message, multiple parallel subagents):** Identify all areas of the codebase you need to understand and launch haiku/sonnet explorers for each in a single message. Don't read one area, then decide to read another — anticipate what you'll need and launch all explorers at once.

2. **Synthesize:** When all subagent results return, cross-reference their summaries in the main conversation. This is where opus earns its cost — connecting findings across areas.

3. **Decide:** Write the plan based on synthesized understanding.

**Example — planning a feature that touches models, serializers, and frontend:**
```
Single message with 3 parallel subagents:
  → haiku: "Find all model files in taskit-backend, list fields and relationships"
  → haiku: "What serializers exist? List their fields and any custom logic"
  → haiku: "Read the frontend types and API service files, summarize the data contract"
Wait for all 3 → synthesize → write plan
```

**NOT this** (sequential anti-pattern):
```
→ haiku: "Find model files" → wait → read result
→ haiku: "Now find serializers" → wait → read result
→ haiku: "Now check frontend" → wait → read result
→ finally write plan (3x slower, same result)
```

- **Delegate (haiku)**: "Find all model files in taskit-backend and list their fields", "What serializers exist and what do they expose?", "Read orchestrator.py and summarize the public API"
- **Delegate (sonnet)**: "Analyze how the harness registry pattern works across 4 files and describe the contract", "Review the test structure and identify what's covered vs. gaps"
- **Keep in main conversation**: Synthesizing subagent findings into an architectural decision, choosing between approaches, writing the actual plan, resolving conflicting patterns

### Implementation mode — Test-First Wave Execution

When executing a plan, **enforce TDD through scope-isolated subagent dispatches.** The key constraint: the test-writing subagent never receives implementation details, and the implementation subagent doesn't start until failing tests exist on disk.

**Why scope isolation, not instructions:** Telling an agent "write tests first" in the same context as implementation details reliably fails — the LLM's prior toward "build the thing" overrides procedural instructions. The fix is structural: the test-writing agent *cannot* write implementation because it doesn't have implementation context. The implementation agent *cannot* skip tests because it literally runs after them.

**The test-first wave pattern:**

1. **Wave 0 — Scenario matrix** (main conversation): List happy paths, edge cases, failure modes. This is the test spec. No implementation design yet.

2. **Wave 1 — Tests only** (subagent, sonnet): Dispatch a subagent whose ONLY job is writing tests. Its prompt contains:
   - The scenario matrix from Wave 0
   - Existing test patterns/conventions (discovered by a haiku explorer)
   - The *behavioral requirements* (what the feature should do)
   - **NOT** how to implement it (no model designs, no serializer details, no view logic)

   The subagent writes test files and returns. Nothing else.

3. **Gate — Run tests, confirm they fail** (main conversation): Execute the test suite. Every new test must fail. If a test passes before implementation exists, it's a false positive — fix or delete it. This gate is mandatory. Do not proceed to Wave 2 until failing tests are confirmed.

4. **Wave 2 — Implementation** (subagents, sonnet/haiku): Now dispatch implementation subagents. They receive:
   - The failing test files (so they know what to satisfy)
   - The behavioral requirements
   - Implementation context (existing models, patterns, etc.)

   **Maximize parallelism within the wave.** Launch all implementation subagents that touch different files in a single message. Only serialize steps that have true data dependencies (e.g., model must exist before serializer can reference it). Within a wave, the default is parallel — sequential is the exception.

5. **Wave 3 — Verify** (main conversation or haiku): Run the full test suite. All tests must pass. Then verify live.

**Example — adding a new feature with model + serializer + view + test:**
```
Wave 0 (main):           Scenario matrix — 3 happy paths, 2 edge cases, 2 error cases
Wave 1 (sonnet):         Write test file with 7 test cases (receives requirements ONLY, no impl details)
Gate:                    Run tests → 7 failures confirmed
Wave 2a (single message, parallel subagents):
  → sonnet: Create model + migration
  → sonnet: Write URL routing (can be done without model)
  → haiku:  Add frontend type definitions
Wave 2b (after 2a completes — depends on model):
  → sonnet: Write serializer + view (reads model from 2a)
Gate:                    Run tests → 7 passes confirmed
Wave 3 (haiku):          Verify live
```

**Maximize wave parallelism by analyzing dependencies.** Before dispatching a wave, map out which steps depend on which. Steps with no dependencies on each other go in the same message as parallel subagents. Steps that depend on prior outputs go in the next sequential wave. The goal is the fewest sequential waves with the most parallelism within each.

**The information boundary is the enforcement.** Wave 1's subagent literally cannot produce implementation code because it wasn't given implementation context. This is not a suggestion — it's a structural constraint.

**When NOT to wave**: If the task is a single-file change, a bug fix requiring investigation, or anything where the steps are inherently sequential — just do it directly. Waving a 2-step task adds overhead without benefit.

**Conflict prevention**: Never put two steps that modify the same file in the same wave. If in doubt, make it sequential. The cost of a merge conflict far exceeds the cost of a sequential step.

## Hierarchical Documentation

This is a monorepo. Each project has its own `CLAUDE.md` (or `claude.md`) and `AGENTS.md` with project-specific commands, architecture, and guidelines. Claude Code auto-discovers these when you run from a subdirectory — the root file provides only repo-wide context.

## Data Inspection & Debugging

**Never use `curl`, raw API calls, or direct HTTP requests** to inspect TaskIt data. The API requires Firebase auth. **Always use the diagnostic scripts** in `taskit/taskit-backend/testing_tools/` — they use Django ORM directly (no auth needed) and produce richer output than the API.

### The scripts

All scripts run from `taskit/taskit-backend/`. Every script supports output modes for token efficiency:

```bash
cd taskit/taskit-backend

# Default (standard) — full human-readable output
python testing_tools/task_inspect.py <task_id>
python testing_tools/spec_trace.py <spec_id>
python testing_tools/board_overview.py [board_id]
python testing_tools/reflection_inspect.py <report_id>
python testing_tools/snapshot_extractor.py <spec_id> <output_dir>

# Brief — 1-3 lines: status + metrics + problem count (~5x fewer tokens)
python testing_tools/task_inspect.py <task_id> --brief
python testing_tools/spec_trace.py <spec_id> --brief

# JSON — structured output, most token-efficient for LLM consumption
python testing_tools/task_inspect.py <task_id> --json --sections basic,tokens

# Full — everything including full comment content and metadata
python testing_tools/task_inspect.py <task_id> --full

# Sections — cherry-pick which sections to include
python testing_tools/spec_trace.py <spec_id> --sections tasks,problems
python testing_tools/reflection_inspect.py <report_id> --sections verdict,diagnosis

# Slim snapshots — exclude large text fields for smaller golden files
python testing_tools/snapshot_extractor.py <spec_id> <output_dir> --slim
```

### When to reach for which script

| You want to... | Use this |
|---|---|
| Understand why a task failed or produced unexpected output | `task_inspect.py <task_id>` |
| Debug a spec run — see what happened, in what order, what broke | `spec_trace.py <spec_id>` |
| Check the state of a board after changes | `board_overview.py [board_id]` |
| Verify a reflection report's verdict, token usage, or quality | `reflection_inspect.py <report_id>` |
| Capture a known-good run as a regression test fixture | `snapshot_extractor.py <spec_id> <output_dir>` |
| Quick status check from LLM agent | Any script with `--brief` or `--json` |
| Check token usage and cost data for a task or spec | `task_inspect.py` (per-task) or `spec_trace.py` (aggregate) |

### Rule: scripts before code inspection

When debugging or verifying behavior, **run the relevant diagnostic script first** before reading source code or logs. The scripts surface problems automatically (invalid transitions, missing fields, empty sections, repeated text) and show the data in context.

For log tailing during live runs (not post-mortem debugging):
```bash
tail -f taskit/taskit-backend/logs/taskit.log           # App log
odin logs -f                                           # Follow all running tasks (from working dir)
odin logs debug -f                                     # Tail odin_detail.log (tracebacks)
odin logs -b <board_id>                                # Access logs from any directory
```

## Repository Structure

- **`harness_usage_status/`** — Python CLI for checking AI provider usage quotas.
- **`odin/`** — Multi-agent orchestration CLI (task-board model, suggestive agent assignments).
- **`taskit/`** — Full-stack task dashboard (Django REST + React/TypeScript/Vite).
- **`prompt-library/`** — Reusable prompt templates (separate git repository).

## Testing & Proof

**Tests pass ≠ feature works.** Static tests validate frozen assumptions. Only live verification proves the user can see and use the feature. Every change must close the full loop: **test → fix → verify live**.

The full testing framework, RCA protocol, stale test handling, and quality standards are in **`docs/testing_process/testcase_process_and_philosophy.md`**. What follows is the operational checklist.

### The workflow: Think → Test → Code → Verify

**This ordering is enforced structurally, not by instruction.** See "Test-First Wave Execution" in the subagent section. The test-writing step runs as a separate subagent that has no implementation context, making it impossible to write implementation first. Do not combine test-writing and implementation into the same subagent dispatch.

1. **Scenario matrix** (mandatory, visible in conversation) — happy paths, edge cases, failure modes, integration seams. Not optional. The matrix prevents writing tests that confirm what you're about to implement rather than challenging whether it's correct.

2. **Write failing tests first** (separate subagent, no impl context) — each scenario maps to a test. The test-writing subagent receives ONLY behavioral requirements and existing test patterns. Run the tests. They must fail. A test that passes before implementation is a false positive. **Gate: do not proceed until failures are confirmed.**

3. **Write minimum code to pass** (separate subagent, receives failing tests) — implementation subagents receive the failing test files so they know exactly what to satisfy. Resist the urge to add features beyond scope.

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

## Compounding Knowledge

`/workflows:compound` captures solutions in `docs/solutions/`. Each solution should document **both**:
1. **The technical fix** — exact code changes, error messages, root cause (searchable next time the symptom recurs)
2. **The design principle** — the *why* behind the approach, reusable across contexts (e.g., "information hierarchy via progressive disclosure" applies to any sidebar, not just TaskDetailModal)

Solutions in `design-patterns/` are principle-heavy. Solutions in `ui-bugs/`, `runtime-errors/`, etc. are fix-heavy. Both should include the other dimension — a bug fix should note the principle it violates; a design pattern should include concrete code.
