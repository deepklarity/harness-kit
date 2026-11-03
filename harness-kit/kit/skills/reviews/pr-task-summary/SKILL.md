---
name: pr-task-summary
description: "Turn a pasted list of people, tasks, PR links and statuses into one HTML page grouped by person, with a Status and an Action line per task. Triggers on: 'standup update', 'who's working on what', 'summarize open PRs', or /pr-task-summary. One PR in depth is /hk-pr-review; this is many PRs at a glance."
argument-hint: "<pasted list of people, tasks, PR links and statuses>"
---

# PR / task summary

Turns a list of tasks, PR links and statuses, usually pasted from Slack, into one page grouped
by person that someone can send to their team or their manager. Every task gets a sentence on
what it is, its status in the author's words, and the one thing that needs to happen next.

Two files drive it, both at the repo root:

- `docs/playbook/tone_and_taste.md` — the house voice. Read it before writing any sentence.
- `docs/skills/knowledge/hk-writeup/SHELL.html` — the page shell the content drops into.

## Workflow

1. **Parse the input.** The paste usually looks like this, with the formatting varying (bullets,
   bold, backticks, emoji):

   ```
   @Person Name
   Open PRs
   * Task title
   Links: [label](url), [label](url)
   Status: `some status text`

   Closed PRs
   * Task title
   Status: `Merged`

   @Next Person
   ```

   Parse by the recurring pattern of title → links → status, not exact syntax. Per person, take
   the name; per task, the title, the PR links (label and url), the raw status text, and a Slack
   thread link if there is one. Two tasks sharing one PR link stay two tasks, each noting the
   other.

2. **Set the period.** Take the date range from the paste if it names one ("Weekly Period: Jul
   29 - Aug 5", "this week's standup") and keep it as `period`, separate from the title. If
   none, leave `period` unset and say so in the reply.

3. **Look each PR up.** Run `gh pr view <url> --json createdAt,title,body,state`. When it
   succeeds, `createdAt` is the opened date, `state` is the live state, and the body feeds the
   summary. When it fails (a repo `gh` cannot reach), leave the opened date unset; a wrong date
   in a page someone shares with their manager is worse than a missing one.

4. **Classify status into one of five buckets.** `blocked` means the fix is needed in this PR;
   `waiting` means it is needed somewhere else.

   - `review` — PR raised, needs review, awaiting review
   - `in_progress` — being worked on, no external blocker
   - `blocked` — issues in this PR itself, not ready to merge
   - `waiting` — fine on its own, stuck on an upstream dependency ("waiting for X to land in dev")
   - `merged` — merged, closed and shipped

   Read the raw status text before picking: "waiting for TEM implementation in dev" is
   `waiting`, not `in_progress`. Keep the raw text; the bucket only decides ordering, and the
   page prints the author's words. If a task has no status, leave status and action empty, set
   the bucket to `in_progress`, and say so in the reply. When `gh` returned `state` MERGED in
   step 3, the bucket is `merged` whatever the paste says, and the Status line reads "Merged
   since this was written".

5. **Write a one- or two-sentence summary per task** on what the task or PR does. Build it from
   any description in the paste or the PR body from step 3, tightened; fall back to the title
   only when there is nothing else, and never just restate the title.

6. **Write an action per task**: what needs to happen next and who does it ("Review needed from
   Harsh", "Waiting on TEM implementation in dev", "Developer needs to resolve the reported
   issues"). If nothing more specific than the status is known, the status text is the action.
   Merged tasks get no action.

7. **Fix typos and casing.** Keep the author's title words, fix spelling ("codgen" → "codegen"),
   and write status and action as plain sentences ("Review needed from Harsh", "PR raised,
   awaiting review").

8. **Build the page.** Read `SHELL.html`, replace `<!-- CONTENT -->` with the content below and
   `<title>Writeup</title>` with the real title, and replace the footer's `.said` sentence with
   one saying the page was built from a pasted list and which PRs `gh` could and could not
   reach. Change nothing else in the shell.

   - Kicker "Team PR summary", the title as `h1`, and a `<p class="lede">` with "Covering
     <period>" when the period is known.
   - One `<section>` per person, in the paste's order, with the name as `h2` and a `.count` of
     how many tasks are active and how many merged.
   - Under each person, one entry per task, active first in the order blocked → review →
     waiting → in progress, merged tasks last. Each entry is a short block: the title in bold
     with the PR links (and the Slack thread link) beside it, "Opened <date>" when known, the
     summary sentence, then **Status:** with the raw text and **Action:** with the action.
     Merged entries carry the title, links and summary, then **Status:** Merged.
   - A task sharing a PR with another says so in one sentence: "Same PR as <other task>."

   Write it to `writeup/pr-task-summary.html` at the root of the repo this skill is installed
   in, `$(git rev-parse --show-toplevel)/writeup/`, gitignored; create the directory if it is
   missing. A re-run replaces it; the period lives in the lede, not the filename.

9. **Reply with the path**, how many people and tasks the page holds, which PRs `gh` could not
   reach, and whether the period was set. The HTML does not go into the chat.
