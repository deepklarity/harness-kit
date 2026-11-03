# The setup journey

Seven stages. `harnesskit init` prints this list; the setup agent opens every message to the person with it, marking each stage from the board: `✓` when all its rows are done, `▸` for the stage in progress (with "n of m"), `○` for the rest. One line under the list says what is happening now and what the person needs to do, or "nothing, I'm working".

| # | Stage | What the person sees | Board rows |
|---|---|---|---|
| 1 | Install | `init` wrote docs/ and writeup/ only | |
| 2 | Your answers | Four questions with proposed answers, then skills and guard | |
| 3 | Look around and score | The repos found, twelve areas scored | t-survey, t-guard, t-checklist, t-wf-skills, t-wf-guard |
| 4 | Agent entry file | The draft, then a diff to approve | t-entrypoint, t-decisions, t-wf-claude-md |
| 5 | Build the harness | Breadcrumbs, scripts, tests, gate, one row at a time | t-board, t-build |
| 6 | Check and hand back | The audit score, AGENTS.md and .gitignore to approve, guard removed | t-harness-audit, t-wf-stop |
| 7 | Prove it | Scenarios and cost to confirm, then the before/after page | t-scenarios, t-prove-confirm, t-prove |
