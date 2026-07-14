# IDEA BRAINSTORMING

Ideas from outside the kit. Practitioners' writing, popular repos, new
techniques. Scout agents fill this in, we read it together, the good ideas
get promoted to the backlog and the rest get deleted. This file is a working
surface, not a museum.

The filter: an outside idea only earns a spot if it plausibly moves a bucket
score in [`fable_roadmap.md`](fable_roadmap.md). Popular is not a reason.
"Moves Memory from 2 to 5" is a reason.

Each entry, short:

- **What it is.** One or two sentences.
- **Who is behind it and where.** Link.
- **Which bucket it serves.**
- **Adopt or borrow.** Can we plug it in, or take the idea and build our own?
- **Honest integration cost.** Plug and play usually means plug and three
  days of glue. Say so.

## How to run a scout wave

Dispatch exploration agents with web access, ideally overnight. Reading
targets to start with (add more as we learn who is worth reading):

- Practitioner writing on AI-assisted engineering: Addy Osmani, Boris
  Cherny, the Anthropic and Cursor engineering blogs, simonwillison.net.
- Trending repos around agent tooling: memory for agents, trace viewers,
  eval harnesses, multi-agent coordination, cost tracking.
- What teams running agent fleets in production say about failure handling,
  review, and routing.

## Entries

Scout wave one, 31 entries read across `docs/wiki/`. Below are the ideas
that survived the filter. Ideas that just restate what a bucket already has
in flight (Trust's five running tasks, Memory's task twins, Watching's
mission control) got skipped or folded into the closest existing idea
instead of duplicated.

- **Advisor pattern for judgment gates.** Anthropic's advisor tool pairs a
  cheap executor (Sonnet or Haiku) with an Opus advisor it can call only at
  decision points, capped by `max_uses`, billed separately so the split is
  visible. Reported cost drop, 11.9%, with better results than the cheap
  model alone. Routing and cost is 3/10 today and its ideas (shadow runs,
  league table) compare whole models against each other. This is different,
  it's a cheaper way to run one task: most of it on Haiku, escalate to Opus
  only when the executor hits something it can't resolve, like a stuck
  review or an ambiguous merge conflict. Borrow the pattern, build our own
  gate list of where escalation is worth it. Cost: wiring the advisor tool
  into the subagent dispatch path and splitting cost tracking into executor
  tokens vs advisor tokens, a day or two of glue.
  (wiki: agent-practices/the-advisor-strategy.md)

- **Tokenizer ratio in the cost math.** Opus 4.7's tokenizer counts about
  1.46x more tokens than Opus 4.6 for the same prompt, so two models priced
  the same per token can cost 40% apart in practice. Our cost-per-task math
  and any routing decision that compares models on sticker price is wrong
  until it accounts for this. Cheap to check: run the same task through two
  models, compare the actual billed tokens in the trace, not the quoted
  price. Adopt as a standing step whenever a model version changes.
  (wiki: agent-practices/claude-token-counts-tokenizer-inflation.md)

- **Cache hit ratio as a watched number.** Claude Code caches by matching an
  unchanged prefix, cache hits cost about 10% of normal input tokens, cache
  writes cost more, and switching model or effort level mid-session throws
  the whole thing away. `cache_read_input_tokens` and
  `cache_creation_input_tokens` are already in every response. Routing and
  cost has no idea that watches this. Add it: flag sessions with high cache
  creation and low cache reads, that's churn from switching models or
  effort levels mid-task for no reason. Nearly free, the fields already
  exist, this is a script reading them.
  (wiki: agent-practices/prompt-caching-essentials.md)

- **Load the whole task spec up front, then leave it alone.** Two
  independent write-ups agree: sending a complete task spec once beats
  drip-feeding follow-ups (each follow-up recomputes the cache), and past
  15-20 messages a fresh session with a one-paragraph summary beats
  continuing the old one. Getting oriented is 4/10 and already tracks
  orientation waste, but its ideas are about maps and briefs, not mid-task
  session hygiene. Add a rule of thumb to how tasks get dispatched: full
  spec in the first message, and a session that's dragging past ~20 turns
  gets summarized and restarted rather than left to bloat. Cheap, it's a
  dispatch habit, not new code.
  (wiki: agent-practices/slash-claude-token-usage-80-90.md,
  agent-practices/best-practices-opus-4-7-claude-code.md)

- **A local model as the free tier.** Sebastian Raschka ran Ollama plus
  Qwen locally and got 20-30 tokens/sec with no rate limit, matching paid
  services on 4-5 of 5 benchmark tasks. Separately, a Hugging Face project
  argues harness quality moves the needle more than swapping models.
  Project powers is 1/10, the kit only knows how to build itself, and every
  task still goes through paid APIs. Don't trust local models for anything
  that merges yet, but pilot one for the cheapest tier of work we already
  don't pay much attention to: exploration subagents, audits, throwaway
  scratch work. Borrow, don't build a serving stack. Cost: real, someone
  has to stand up Ollama and pick a model, call it a day of setup plus
  ongoing babysitting of a new failure mode (local model gives a worse
  answer and nobody notices).
  (wiki: harnesses/local-coding-agents.md, harnesses/harness-optimization.md,
  building-agents/running-local-models-is-good-now.md)

- **A security-shaped watchdog rule, separate from the flaky one.**
  Anthropic's and Cloudflare's own red-team reports on their newest model
  say the same thing two ways: it can now chain vulnerability primitives
  into working exploits at a scale prior models couldn't, and its safety
  refusals are inconsistent, the same request framed two ways gets two
  different answers. Trust's watchdog work in flight right now is about
  false alarms (three in one hour), that's a different failure mode from
  this. Any agent that gets shell or network access needs a rule that
  flags privilege-escalation-shaped tool calls or repeated probing
  patterns, on top of the calm-watchdog fix, not instead of it. Cost:
  needs someone to write down what "probing" looks like in our own tool-call
  logs before this is buildable, that's the first task, not a subscription
  we can just turn on.
  (wiki: security/anthropic-mythos-red-team-assessment.md,
  security/cloudflare-cyber-frontier-models.md)

- **Harness changes the security score, not just the review gate.** Endor
  Labs ran the same model, GPT-5.5, through two harnesses and got 23.5%
  security via Cursor against 20.1% via Codex, same model, same task set.
  Audits is 2/10 and mostly reruns metrics scripts by hand. Add a preset
  that scores generated code for known-bad security patterns after any
  change to our own prompts or tool access, not just at merge time, so a
  harness tweak shows its effect on security the same week it ships instead
  of showing up as a bug three months later.
  (wiki: security/endorlabs-gpt55-cursor-code-security.md)

- **A cost-spike trigger, not just a memory-budget fix.** AI inference
  costs roughly a million times more per request than plain HTTP, so a
  single runaway task is a real money event, not just a slow one. Trust's
  in-flight "one memory budget for everything" idea fixes sandboxes filling
  RAM, it doesn't watch dollars. Add a specific number: if a task's token
  spend crosses some multiple, say 5x, of its closest twin's historical
  cost, flag it before it merges, don't wait for the invoice. Needs task
  twins landed first to have a baseline to compare against.
  (wiki: security/vercel-protecting-token-theft.md)

- **Gate by how hard a mistake is to undo, not by task type.** Addy Osmani's
  autonomy levels are keyed on how fast you'd notice an error and how easy
  it is to roll back, not on what kind of task it is. Accenture's shopper
  survey backs this up with a real number: 74% trust an agent for routine
  picks, but only 9% would let it move money on its own, trust drops hard
  right where reversal gets expensive. The human's seat is 2/10 and its
  ideas (one inbox, taste memory) are about where questions land, not about
  which tasks need a human at all. Right now every merge gets the same
  review gate regardless of blast radius. Classify tasks by how expensive a
  wrong merge is to undo (docs and tests are cheap to revert, schema and
  deploy changes are not) and only hold the slow-rollback ones to tight
  review. Cheap first test: exempt one low-stakes task category from the
  full review wait for a week and watch whether the escaped-bug rate
  actually moves.
  (wiki: sources/addy-osmani.md,
  in-the-field/accenture-ai-shopping-agents-trust.md)

- **Don't trust a single review verdict, reframing flips it.** Cloudflare's
  red team found the same request, framed two different ways, got opposite
  safety answers from the same model. Trust is 6/10 and its whole case is
  "the kit must not lie." A reviewer gate that's one prompt, one pass, can't
  assume the verdict would hold up under a slightly different phrasing of
  the same review question, that's a form of lying we haven't checked for.
  Cheap test: take a
  sample of already-reviewed tasks, rerun the review gate with the prompt
  lightly reworded, see how often the verdict flips. If it flips often, the
  gate needs more than one framing before a verdict counts.
  (wiki: security/cloudflare-cyber-frontier-models.md)

- **Let an agent ask for its own fan-out mid-task.** Mollick's report on
  the newest Claude and Google's AI co-scientist paper both describe one
  agent instance spawning and organizing several subordinate instances on
  its own, mid-task, not because a human scheduled a wave. Moonshots is
  0/10, nothing built, and today every wave is planned by us in the main
  conversation before any agent starts. A moonshot-tier idea: let an agent
  request its own sub-fan-out when it hits a parallelizable subproblem,
  logged the same way a human-dispatched wave would be, so it's still
  auditable. Needs Memory and mission control landed first, this is a
  someday idea, not a task.
  (wiki: in-the-field/mollick-working-with-mythos.md,
  building-agents/google-ai-co-scientist.md)

### Contradictions

Four places the outside world disagrees with how we build this kit. Each
one is a real disagreement, not a strawman, so each gets a cheap test
instead of a verdict.

- **StrongDM ships code with no human review at all.** Their "software
  factory" enforces that code is not written or reviewed by humans, and
  instead relies on scenario-based tests and simulated third-party services
  to catch problems. Our whole workflow runs on a reviewer gate. They might
  be right that for well-covered scenarios, automated judgment already
  beats a tired human skimming a diff. Cheap test: pick one low-stakes
  category, doc-only changes say, and merge 20 of them on green tests alone
  with no human review, then compare their defect rate over a week against
  reviewed twins of the same size.
  (wiki: harnesses/software-factory.md)

- **Willison builds first, specs never.** His pattern is "make the
  hallucination real": if the model tries to use a capability that doesn't
  exist yet, build that capability, react to what the agent reaches for
  instead of writing the spec first. We require a scenario matrix before
  any test gets written. For big or risky work that's right, but for a
  one-file bug fix, writing the matrix might cost more than just trying it.
  Cheap test: for the next 10 small single-file fixes, skip the scenario
  matrix, go straight to a failing test and a fix, compare the redo rate
  against spec-first fixes of the same size.
  (wiki: sources/simon-willison.md)

- **Malleable software says the user reshapes the tool at the point of
  use, we say the kit picks defaults and the user overrides.** Ink & Switch
  argues most "customizable" systems, settings panels, plugins, still don't
  let a normal user actually reshape anything without real friction. Our
  default-first principle says the same thing, in effect, if the only
  override path is editing a config file, a non-technical user still can't
  reshape it. Cheap test: pick one default, agent assignment say, and time
  how long it takes someone who isn't a developer to override it without
  touching a file.
  (wiki: harnesses/malleable-software.md)

- **Osmani says autonomy is a per-task choice, we run a fixed pipeline.**
  His levels are chosen per task based on how fast an error would surface
  and how easy it is to undo, not baked into the process. Our test-first
  wave pattern and review gate apply the same way to every task
  regardless of how reversible it is, which is itself a contradiction of
  our own "no hardcoded stages" tenet. Cheap test is the same one as the
  gate-by-reversibility idea above: exempt one fast-rollback task category
  from the standard wait for a week, watch the escaped-bug rate.
  (wiki: sources/addy-osmani.md)

- **Ronacher says abstraction libraries fail, harness-kit is one.** The
  argument, summarized in Willison's write-up: wrapping a platform's SDK in
  your own abstraction always breaks eventually, because you can't keep up
  with what the platform adds, and you should just use the SDK directly.
  Harness-kit is an abstraction over Claude Code and the provider APIs, so
  this argument is aimed straight at the project existing at all, not just
  a design choice inside it. He might be right that every layer we add
  between us and the raw SDK is a future migration we'll owe. Cheap test:
  pick one place we wrap the SDK today (routing, cost tracking) and check
  how much of that wrapper duplicates something the SDK now does natively,
  if most of it does, that's the tell.
  (wiki: sources/simon-willison.md)
