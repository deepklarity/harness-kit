---
name: hk-scout-wiki
description: Run a scout wave over outside sources (links, blogs, trending repos) into a progressive-disclosure research wiki in the current project, then distill findings into the project's ideas/backlog file. Generic across projects. Use when the user shares links/blogs to research, asks to "scout", "build a wiki from these", or wants outside ideas funneled into planning. Triggers on - 'scout wave', 'wiki scout', 'research these links', 'update the wiki', or /hk-scout-wiki.
---

# Scout wave → wiki → ideas (project-agnostic)

Three phases. Phases 1-2 are cheap (haiku); phase 3 needs judgment (sonnet
or the main conversation). The wiki is evidence; the ideas are the product.

## Phase 0 — Discover the project (once per session)

- Wiki root: existing `docs/wiki/` (or wherever a wiki README already
  lives). None yet → create `docs/wiki/README.md` with: a category table,
  the entry format below, and grep/tag conventions. Never a second format.
- Project context: read the project's CLAUDE.md / roadmap / ideas file to
  learn (a) what the system IS in one line, (b) the named areas/buckets/
  themes work is organized under, (c) where distilled ideas belong (an
  IDEAS/BRAINSTORM file, a backlog, or — if none — propose one). If the
  project defines a writing tone doc, read it and obey it.

## Entry format (uniform, always)

```markdown
# <Title>
source: <url>
author: <who>  |  fetched: <yyyy-mm>  |  status: read | unreachable
tags: <comma, separated, lowercase-hyphenated>

**One line.** <what you'd tell a colleague>

## TL;DR
3-6 plain sentences.

## Key ideas
Self-contained bullets.

## Project hooks
Where this touches THIS project: named areas, files, contradictions worth
arguing about. Honest "nothing yet" allowed.
```

## Phase 1 — Scout (parallel haiku agents)

1. Partition sources into batches of 4-8 by theme; each theme is a wiki
   category directory with its own `TOC.md`. Whole-blog surveys go in
   `sources/` (survey = recurring theses + best posts, one line each).
2. Launch ALL batches as parallel haiku general-purpose agents in ONE
   message. Every prompt includes: the entry format verbatim, the fetch
   list, the project-context line from Phase 0, the unreachable rule
   (retry once, then write the entry with `status: unreachable` — NEVER
   invent content), and the order to write the category `TOC.md`
   (`- [title](file.md) — one line. tags: ...`).
3. No explicit link list ("trending X")? One haiku agent with WebSearch
   builds the URL list first; then partition.

## Phase 2 — Assemble (main conversation, minutes)

- Spot-check 2-3 entries for format drift; fix entries, not the format.
- Ensure every category TOC exists and its links resolve; write any TOC a
  scout couldn't own (shared directories).
- Commit the wiki separately from the distillation.

## Phase 3 — Distill (one sonnet agent, or main conversation)

Read the new entries' TL;DR + hooks (not the sources again) and append to
the project's ideas file, in that file's existing structure and tone:

- One paragraph per idea, graded honestly against where the project
  stands, citing the wiki entry, naming the area it would move.
- Contradictions with the project's philosophy are wanted — state them
  plainly and why the outside view might be right.
- Distillation means "what should WE do", never "what did they say".

## Cost discipline

Scouts read the web, not the repo. Distillation reads the wiki, not the
web. Nothing here needs opus. ~30 links ≈ one mid-size task execution.
