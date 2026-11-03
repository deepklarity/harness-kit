# Applying the CLAUDE.md template to a workspace

How to turn the kit's CLAUDE.md template into this workspace's real entrypoint,
whether or not it already has one. Read the whole file before you start writing.

## 1. Read what is already there

Read the workspace's `CLAUDE.md`, `AGENTS.md`, and any per-repo copies, in full,
before you write a word. Note which one the person actually maintains — the one
with recent commits and real commands is primary, whatever it is called. If there
is nothing, you are writing fresh from the template and every section is FILL.

## 2. Map their sections to the template's

Write the mapping down for yourself first: for each heading in their file, which
template section covers the same ground. Three outcomes.

- **Same ground** — the template section is where their content goes.
- **Ours only** — a template section they have nothing for. Fill it.
- **Theirs only** — a section the template does not have. It goes in at the end under
  its own heading if it is a standing rule; if it is a mechanism or a list, it goes
  into the doc that owns that topic and the file keeps one line pointing at it.

## 3. Apply the marker on each template section

Every template section opens with an HTML comment saying FILL, COPY or BLEND.

- **FILL** — write it from what you found in the workspace: the folder listing,
  the start script, docker-compose, CI config, `.env.example`, the router. Each
  `{{slot}}` says where its answer comes from. A slot you cannot answer from the
  code is a question for the person, not a guess.
- **COPY** — a kit rule. Keep it word for word. If the workspace has no
  `docs/patterns/` or `docs/breadcrumb_analysis/` yet, the rule still goes in and
  the missing folder becomes a row on the readiness board.
- **BLEND** — merge their content with the template's. Their specifics win over
  the template's placeholders; the template's rules fill what they never covered.

**Everything you pull from their file is a claim, not a fact.** Check each line against
the code before it goes in; the claims check in `docs/harnesskit/entry.md` runs over
the whole draft, blended lines included.

**Commands stay verbatim.** If their file documents a command and that command
still works, keep the exact string. Do not retype it in the template's style, do
not "improve" a flag. Check it runs first; a command that no longer works becomes
a question, not a silent deletion.

## 4. Conflicts go to the person, batched

A conflict is when one of their rules and one of the kit's rules cannot both be
true — they say plans live in `docs/plans/`, the kit says `writeup/<initiative>/`;
they route everything to one model, the kit routes by tier.

Do not resolve it silently, in either direction. Collect every conflict as you go,
and put them all to the person in one message at the end of the pass — one question
each, each with the answer you propose and one sentence of why. Not one question at
a time, and never a question with no proposal attached.

> Plans: your file puts them in `docs/plans/`, the kit puts them in
> `writeup/<initiative>/` so they stay out of git and next to the board.
> Proposed: move to `writeup/`, leave the existing plans where they are.

**A rule the person did not agree to drop becomes a question, not a deletion — and a
question with a proposal.** When the draft is over 150 lines, the rules you propose to
cut go in the same batched message as the conflicts, each with where it is moving to:
a mechanism moves into the doc that holds it, a one-off moves to the initiative's
board. Nothing is dropped on the floor; everything lands somewhere named.

## 5. AGENTS.md

Produce `AGENTS.md` as a one-line pointer:

```
See [CLAUDE.md](CLAUDE.md) for guidance when working in this repository.
```

Reverse it if the workspace already treats `AGENTS.md` as primary — then the
content lives in `AGENTS.md` and `CLAUDE.md` is the one-line pointer. Ask which
way round if both files exist with real content in each.

## 6. Audit every line, then cut to 150

The finished file is 150 lines or fewer, counted before you hand it over. This is a
hard bound, not a target: if the draft is 170, you cut 20 lines, and the lines you cut
are mechanisms that a doc already holds. Everything in this file is believed and
followed, and nothing in it is verified: a mechanism spelled out here stops the
agent reading the doc that holds the fuller answer. Measured on one workspace,
the same question at the same model answered
from this file with no doc opened while the section describing the mechanism was
in it, and went to the skill and the doc, faster and better, once the section had
been cut back to its rule and a link.

So read the draft line by line and put each line through two tests.

- **Is this a rule or a link, or is it a mechanism a doc already holds?** A
  mechanism moves into the doc; the section keeps the standing rule, with its why
  in one sentence, and one link.
- **Would this line still be right with a thousand of that thing?** Name the
  catalogue, never the items in it: "each repo's debug scripts are catalogued in
  its README with the complaint each answers", not five invocations; "one class
  is the entry point and the repo's own CLAUDE.md names it", not a list of
  function and constant names.

What survives is plain facts, the standing rules in their sections, and one entry
link per area — the docs index for the tree, the breadcrumb index for flows, the
testing doc for tests. Never a per-topic list of doc file names, never a hook
script path, never a paragraph telling the model how to think. Those paragraphs
do not hold: a "cite only what you opened" rule was added to one workspace's
entrypoint and the next run cited eleven files it had never opened. Trimming a
mechanism changed the behaviour; wording it differently did not. Measure the leak
and report it; do not spend loops on the wording.

The glossary belongs in `docs/glossary.md` behind the docs index, not in a section
here, and the never-do rules sit in the sections they belong to rather than in a
list of their own.

## 7. A look-up only happens when it is written as a step

An agent opens the docs for a kind of question when the entrypoint states the
look-up as a step for that kind of question. A link at the end of a section does
not do it: on a workspace where breadcrumb-first was a step but scoped to flows,
the flow question opened the breadcrumb index and the testing question, whose doc
was only a "full strategy" link, never opened anything. So the template states
breadcrumb-first for flows, and the same move as a step for testing and debugging
questions. Keep both short, and keep the answer itself out of the section.

## 8. Say what you did

End with a short list, four groups, plain sentences:

- **Kept** — their sections and commands that went in unchanged.
- **Blended** — sections where both contributed, and what each gave.
- **Added** — kit sections they had nothing for.
- **Asked** — the conflicts you put to the person, and what you proposed.

Then apply their answers and update the file in the same turn.
