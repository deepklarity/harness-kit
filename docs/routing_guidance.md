# Routing guidance — one source for operator loaders AND `odin plan`

Anyone assigning tasks (a human, the operator session writing wave loaders,
or `odin plan` composing a spec) reads THIS file plus the live evidence
(`league_table.py --brief`, `routing_suggest`). Hardcoded agent maps in
loaders are the anti-pattern this file replaces — nine waves never assigned
agy because of one copied dict (see buckets/routing-and-cost.md).

## The bench (all five are real options)

| Agent | Models | Lane | Quota reality |
|---|---|---|---|
| glm | glm-5.2 | parallel workhorse: implementation, fixes, full-stack | paid plan, deep |
| minimax | MiniMax-M3 | parallel workhorse: implementation, measurement, sweeps | paid plan; stream-error loops when exhausted — reassign, don't retry-storm |
| claude | sonnet-5 | judgment-heavy: design-laden briefs, cross-system changes, reviews | keychain token, self-refreshing |
| codex | gpt-5.4 | second-opinion implementation; check quota before dispatch | subscription |
| agy | Gemini 3.5 Flash (High) | browser work: screenshots, UI proof, visual verification; light mechanical tasks | **5-hour rolling quota** (NOT weekly — corrected) — fine for regular small/medium tasks, just not sustained parallel floods |

## Rules of thumb

1. Ask the evidence first: `routing_suggest` ranks by measured cost/success;
   the league table shows who lands what. Override with a reason, and record
   it (assignment WHY, task 234).
2. Spread the bench. A provider outage (minimax, today) should never stall a
   wave — if one provider holds >60% of a wave's tasks, rebalance.
3. agy gets UI-proof and screenshot tasks BY DEFAULT — that is its lane and
   nobody else's. Its 5-hour quota comfortably covers a few tasks per wave.
4. Reviews: board setting (`reflection_model`, currently sonnet by user
   call); the size-scaled strategy is the measured cost option (task 204).
5. Escalate on evidence, not in advance: cheapest capable first; a real
   failure moves the task up one tier with the failure cited.

## For `odin plan`

Planner prompts should include this file (it is small on purpose). When the
planner proposes assignments it names the lane rule it applied per task.
