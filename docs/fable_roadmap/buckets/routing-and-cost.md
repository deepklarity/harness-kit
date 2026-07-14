# Routing and cost

**Why.** Every task costs real money and real minutes, and today the choice
of who does it is a gut call. With the cost data we already capture, that
choice can be measured, visible, and honestly fun to watch.

**Ideas.**
- Shadow runs. When the machine is idle, re-run yesterday's completed task
  on a cheaper model and have the reviewer score it against what shipped.
  Free A/B tests of opus vs gpt vs haiku from real work, no synthetic
  benchmarks.
- A league table. Models earn harder tasks on streaks and get benched on
  failures. Same math as routing, but you can watch it like a season.
- Speed is a cost. Tokens per second, per provider, per time of day. Slow
  hours are real, dispatch around them.

When an idea here becomes real work, it gets its own section below this
line (or its own file) with the design, the tasks, and the before and after
numbers. The WHY above travels into every task description.

## Loader assignments are still opinion (user catch)

Nine waves of loader scripts hardcoded glm/minimax/claude maps; agy/gemini
never received a task — not by evidence, by authoring habit. The suggester
(188/234) can rank all five providers but the loaders never ask it. Fix
when tasked: wave loaders (and rework-by-conversation) take assignments
from routing_suggest, operator overrides recorded as such. Near-term: agy
debuts on the proof-screenshots task (browser capture is its lane), and
today's minimax quota outage is the standing argument for a wider bench.
