---
name: pr-task-summary
description: Turns a raw list of team tasks, PR links, and status notes (e.g. pasted from Slack, a standup update, or a "who's working on what" dump) into a polished shareable HTML report — grouped by person, with a short plain-English summary of what each task/PR is about, an explicit "Status:" and "Action:" line, color-coded status pills, filters, sorting, and links to the PRs and Slack threads. Use this whenever the user pastes PR links with task titles/status and asks for a summary, digest, report, standup update, or "something I can share" — even if they don't say "skill" or "html" explicitly. Also use for requests like "summarize open PRs", "make a status update for the team", or "turn this into a report".
---

# PR / Task Summary Report

## Overview

Converts a messy list of tasks + PR links + statuses (usually copy-pasted from Slack) into a clean, single-file HTML report grouped by person, ready to share.

Three files in this skill's base directory drive it:

- `SKILL.md` — workflow and reporting rules.
- `TEMPLATE.html` — the self-contained report template with a JSON data island.
- `report.example.json` — a complete fake report with two contributors and every supported field.

## How to use

1. Read `TEMPLATE.html` from this skill's base directory.
2. Read `report.example.json` if the JSON shape is unclear.
3. Build the report JSON.
4. Replace only the `<script type="application/json" id="pr-summary-data">…</script>` block with pretty-printed JSON. Escape `</script>` as `<\/script>` inside the JSON string before embedding.
5. Ensure `docs/team-pr-summary/` exists in the active repo.
6. Write the result to `docs/team-pr-summary/<report-name>.html`.
7. Give the user the generated HTML path. Do not paste the HTML into chat.

## Workflow

1. **Parse the input.** The user will typically paste something shaped like:
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
   ...
   ```
   Extract, per person: their name, and for each task: title, PR links (label + url), raw status text, and — if present — a Slack thread link. Formatting will vary (bullets, bold, backticks, emoji) — parse loosely by looking for the recurring pattern of title → links → status, not exact syntax. If two tasks share the same PR link (common — one PR can close two tickets), keep them as separate task entries; the renderer auto-detects and badges shared PRs.

2. **Get the analysis period.** Ask the user (or infer from context, e.g. "Weekly Period: Jul 29 - Aug 5, 2026", "this week's standup", or a date range they mention) what window of activity this report covers, and set `period` at the top level exactly to that date range (e.g. `"Jul 29 - Aug 5, 2026"`). Do not put the period in `title`. This is distinct from `generated_date` (when the report was built) — the period is *what range of work is being summarized*.

3. **Get each PR's opened date if available.** Set `opened_date` per task. Don't guess or fabricate this — if the user's paste doesn't include it and the repo isn't reachable (most of these are private, so `web_fetch` on the PR URL will typically 404), leave `opened_date` unset rather than inventing a plausible-looking date; a wrong date in a report someone shares with their manager is worse than a missing one. Ask the user for it if it matters for this report, or simply proceed without it.

4. **Classify status into one of FIVE buckets** (`status_type`). Keep `blocked` and `waiting` separate — they mean different things and conflating them was a real bug caught in review:
   - `review` — PR raised / needs review / awaiting review
   - `in_progress` — actively being worked, no external blocker
   - `blocked` — issues exist in *this* PR itself, not ready to merge
   - `waiting` — otherwise fine, but stuck on an external/upstream dependency (e.g. "waiting for X to be implemented elsewhere", "not mergeable until Y ships")
   - `merged` — merged, closed and shipped

   Read the raw status text carefully before picking a bucket — "waiting for TEM implementation in dev" is `waiting`, not `in_progress`, even though older/lazier parsing might reach for "in progress" as a catch-all. When genuinely ambiguous between `blocked` and `waiting`, ask: is the fix needed in this PR (`blocked`) or somewhere else (`waiting`)?

   Always keep the raw status text too — the bucket only drives the pill color and sort order, it doesn't replace the original wording. It's rendered verbatim as "Status: <raw text>".

5. **Write a short summary per task** (1–2 plain-English sentences on what the task/PR actually does). If the user's paste already includes any description, context, or explanation for the task beyond just the title (e.g. a sentence in Slack, a PR/Trello description, a comment explaining what it does), use that as the basis for the summary — condense/tighten it rather than discarding it and re-deriving from the title alone. Only fall back to inferring purely from the title when no such description was given. Either way, don't just restate the title verbatim. Do NOT fetch the PR URLs — these are almost always internal/private repos that can't be reached anyway.

6. **Write an `action` field per task** — a short, specific "what needs to happen next and who" line (e.g. "Review needed from Harsh", "Waiting on TEM implementation in dev", "Developer needs to resolve reported issues"). This is the single most useful line in the report — it's what tells a reader what to actually do, as opposed to just what state things are in. If nothing more specific than the status is known, it's fine to reuse the status text as the action. Omit `action` for merged tasks (no action needed).

7. **Apply consistent wording** to titles and status text before rendering — Title Case for task titles, plain professional phrasing for status/action text (e.g. "Need Review from Harsh" → "Review needed from Harsh", "PR Raised, Need review" → "PR raised; awaiting review", "codgen" → "codegen"). Don't just pass through the user's raw casing/typos.

8. **Build the report JSON.** Match the shape in `report.example.json`. Use fake data only from that file as a schema guide; replace it with the user's actual contributors, tasks, PR links, statuses, actions, dates, and Slack thread links.

9. **Build the page:**
   1. Read `TEMPLATE.html`.
   2. Replace only the `<script type="application/json" id="pr-summary-data">…</script>` block with the report JSON, escaping `</script>` as `<\/script>`.
   3. Write the result to `docs/team-pr-summary/<name>.html`.

   Create the repo-local output folder if needed:
   ```bash
   mkdir -p docs/team-pr-summary
   ```
   Pick `<name>` based on context (e.g. `team-pr-summary-2026-08-03.html`).

10. **Present the file path** to the user. Don't paste the HTML into chat.

## What the renderer already handles automatically (don't rebuild these per-request)

- **"Status:" / "Action:" lines** on every non-merged card, bold-labeled.
- **"Opened <date>" line** per task when `opened_date` is supplied, and a report-wide "Covering <period>" line in the header/footer when `period` is supplied — both blank/omitted if not given, never fabricated.
- **Sorting** — within each person, active tasks are auto-sorted blocked → needs review → waiting → in progress, so the most urgent items surface first. Merged tasks are pulled into a collapsed `<details>` group per person so they don't crowd out active work.
- **Contributor summary line** — e.g. "1 awaiting review · 1 blocked · 1 merged" instead of a flat open/closed count.
- **Status and Contributor filter chips** — each shows a live count, multi-select, with a "Clear filters" button that only appears once something's active, and an `aria-live` result count.
- **Linear / Board view toggle** — the "Linear"/"Board" buttons themselves stay, but the "View" group label above them was removed per mentor feedback (redundant). Board = side-by-side scrollable columns per person (with a compact status badge row at the top of each column), state is kept in the URL (`?view=board`), not localStorage (localStorage/sessionStorage don't work in this environment).
- **Shared-PR badge** — if the same PR url appears under two different tasks, both get a small "shared" tag with a tooltip naming the other task.
- **Color-coded left border per status**, reduced-but-still-readable opacity on merged cards, sticky filter bar.
- **Accessibility** — semantic `h2`/`h3` headings, `aria-pressed`/`aria-checked` on toggles, `aria-label` on PR/Slack links, visible focus rings, `aria-live` result count.

Don't restyle or re-implement any of the above per-request — just replace the JSON data island in `TEMPLATE.html`. If the user wants a different visual theme, edit `TEMPLATE.html` directly rather than generating a one-off design.

## Notes / known gaps (ask or note if the user wants these)

- No live GitHub PR state (Draft/Open/Approved/etc.) — would need API/auth access to the repo, which these internal repos don't allow from this environment.
- No per-task "last updated" timestamp — add a `last_updated` field to the JSON and wire it into `render_task` if the user starts providing this.
- Sorting is fixed (urgency-based); no user-facing sort control yet beyond that default.
- If a person has only open or only closed tasks, pass an empty list for the other — the template handles it.
- If the user's paste is missing a status, leave `status`/`action` empty and default `status_type` to `in_progress` rather than blocking on it — this is a low-stakes internal doc, keep momentum and note the assumption briefly in your reply.
- **Preview sandbox gotcha**: when this HTML renders inside Claude.ai's own inline preview, the page runs in a `srcdoc` iframe with no real URL, so any `history.replaceState`/`URL` calls must be wrapped in `try/catch` (already done in `TEMPLATE.html`) or they throw and can break the page. Keep that guard if you touch the view-toggle JS.
