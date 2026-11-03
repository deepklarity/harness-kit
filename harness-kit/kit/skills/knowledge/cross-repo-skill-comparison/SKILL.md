---
name: cross-repo-skill-comparison
description: "Compare the Claude skills across two or more repos (paths or GitHub links) and write one HTML page: which skills exist where, which copy is newest, what each fork changed, which are worth pulling into this repo. Triggers on: 'compare skills across repos', 'which repo has the latest skill', 'sync skills between projects', 'skill drift', or /cross-repo-skill-comparison."
argument-hint: "<repo path or URL> <repo path or URL> [more...]"
allowed-tools: Bash, Read, Write, Grep, Glob, Agent
---

# Which repo has the best copy of each skill

Skills get copied between projects and then drift: each copy gets fixed in its own repo, nobody merges the fixes back, and later three repos hold three versions of one skill with no record of which is good. This skill reads them all and says so on one page.

## Context

<repos> $ARGUMENTS </repos>

If the context above is empty, ask which repos to compare. The repo you are standing in (`git rev-parse --show-toplevel`) is always part of the set; the "worth pulling in" verdicts are written for it.

## Where skills live

For each repo, take every directory that has a `SKILL.md`, looking in this order:

1. `docs/skills/` — this repo and others built like it keep the skill bodies at `docs/skills/<category>/<name>/`.
2. `.claude/skills/` — a `SKILL.md` whose body says "This skill lives in docs/skills/…" is a stub, a pointer to the body already found; skip it. Follow a symlink and say so on the page.
3. Anything `**/SKILL.md` finds that the first two missed, skipping `node_modules`, virtualenvs and vendored copies.

A GitHub URL with no local checkout gets cloned into the scratchpad directory with `git clone --filter=blob:none <url>`: full history, blobs fetched on read, so each directory keeps its own commit date (a `--depth 1` clone gives every skill the tip commit's date; a server that ignores the filter gives a full clone, which also works). Say on the page which repos were read from a fresh clone. In every repo, date a skill by `git log -1 --format=%ci -- <dir>`, never by file times.

## How to compare

**1. Inventory.** Per repo, list each skill: name (frontmatter `name:`, else the directory name), the `description:` line, the file list, total size, and the last commit date. One haiku subagent per repo, all in one message.

**2. Match the skills up.** Two skills match when their names are the same after stripping a prefix (`hk-pr-review`, `pr-review` and `matt-pr-review` are one family). When the names differ but the descriptions do the same job, pair them and say the pairing was by purpose. Everything else is unique to its repo.

**3. Diff each family.** For every skill in more than one repo, `diff -r` the skill directories: the docs/skills body on this side, the skill directory on the other; a stub is never part of the diff. Classify each family:

- **Identical** — byte for byte, nothing to decide.
- **One lineage** — one copy is simply ahead. Name the newest by commit date and say in one sentence what it gained.
- **Forked** — both sides changed since they diverged. Say what each side added, because taking the newest here loses someone's work.

The diff decides the class: one lineage when the older copy's text survives whole inside the newer, so the diff changes one side only; forked when each copy holds lines the other lacks. Two repos with no shared history are still classified this way, on text alone.

Diff hunks stay off the page. The page carries the verdict sentence and which files differ, were added or are missing; anyone who wants the hunks has the two paths.

**4. Judge what is worth pulling in.** For skills missing from the current repo, read each description and sort it into worth pulling (one sentence why, grounded in this repo's stack and what its skills already cover) or not (specific to the other repo, or a duplicate of something here under another name).

**5. Nothing gets copied.** The page is the deliverable; copying, merging forks and deleting stale copies are the reader's decisions. For each worth-pulling skill, put one command on the page that installs it in this repo's layout, body and stub both:

```bash
mkdir -p docs/skills/<category>/<name> && cp -R <other-repo>/<skill-dir>/. docs/skills/<category>/<name>/ && python3 docs/skills/knowledge/hk-skill-creator/scripts/init_skill.py <name> --category <category> --stub-only
```

Pick the category from the six under `docs/skills/` (`docs/skills/README.md`).

## The page

Build it in `docs/skills/knowledge/hk-writeup/SHELL.html` after reading `docs/playbook/tone_and_taste.md`, and write it to `writeup/skill-comparison-<slug>.html` at the root of the current repo, the slug joining the repo names. Use only what the shell styles: `.chips`/`.chip`, `.lede`, `section` with `h2`, `.tblwrap`/`.tbl`, `.kvwrap`/`.kv`, `details.row` with `.row-name`/`.row-status`/`.dbody`, `.cmd`/`.btn`, `.note-line`, `.mono`.

Sections, in this order, dropping any that would be empty:

- **Chips**, one per repo: name and skill count. One `.lede` names each repo's path.
- **Forked skills** first when there are any, one `section` each: what side A changed, what side B changed, what a merge keeps from each.
- **The families table**, a `tbl` inside `tblwrap`: one row per family, one column per repo, each cell one sentence (last commit date, and what this copy lacks or forked). Same table for two repos or ten.
- **Worth pulling in**, one `details.row` per skill: `row-name` is the skill, `row-status` is the one-sentence reason; the body has the description and the install command in a `cmd` row.
- **Unique and staying put**, a `kv` table in `kvwrap`: name, home repo, one sentence of purpose.

Every verdict is a sentence a person can act on without opening a terminal first.

## Report back

One line per repo read, how many families were identical, one lineage, forked and unique, and where the page went. If two repos hold forked copies of the same skill, say so in chat as well; that one should not wait for someone to open the page.
