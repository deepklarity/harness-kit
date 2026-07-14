tags: root-cause, defaults, toil, process-discipline, operator

# Second Friction Means Structural Fix

When the same friction appears a second time, the fix must move into code, defaults, or structure — not into documentation, memory, or one more manual run. A policy written in a doc is a wish; a policy encoded as a default is enforced.

## Why it matters

The cheapest local patch (a doc note, a manual re-run, an ad-hoc DB tweak) makes the friction disappear *for this instance* while guaranteeing it recurs. Across the 2026-07-03→05 operator sessions this was the single most common failure mode: the user had to raise the same complaint up to three times before the fix landed where it could actually hold. Every repeat costs a full round of user attention and erodes trust faster than the original bug did.

## The signal

You are about to do any of these for the **second** time:

- Write a rule into OPERATIONS.md / CLAUDE.md instead of changing the code path that violated it
- Re-run a manual command to restore a state the system keeps drifting out of
- Hand-edit data (DB field, config value) to clear a symptom the code path left behind

Ask: "what code change makes this impossible to need again?" — and file or make that change instead.

## Instances

- **2026-07-04/05 / stale model defaults dispatched three times**: Outdated models (glm-4.7, MiniMax-M2.5) kept being dispatched. Complaint #1 → OPERATIONS.md text updated + manual `odin set-model` per task. Complaint #2 (verbatim repeat, a day later) → same. Complaint #3 ("you're wasting time setting models again, why didnt you make that a default now") → finally a code-level task (#112) to default model resolution from the agent lineup. Two full days of toil that one code fix on day one would have removed.
- **2026-07-04 / stale `merge_error` cleared by hand**: After a successful merge, a stale `merge_error` in task metadata left a misleading tooltip. Fixed with an ad-hoc Django-shell `md.pop('merge_error')` instead of making `merge_task_on_reflection` clear stale errors on success — symptom removed, cause still live.
- **2026-07-05 / sleep-chain waiting after explicit block**: The harness blocked `sleep 150; …` and prescribed the Monitor until-loop pattern. The operator session went on to use near-identical `sleep N; watch_task.sh …` idioms 10+ more times instead of adopting the prescribed pattern once.
