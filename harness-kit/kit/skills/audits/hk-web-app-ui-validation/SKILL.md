---
name: hk-web-app-ui-validation
description: "Walks a running web app in Chrome at three widths and reads its fetching components, then writes up layout defects, dead ends, broken links, forms, accessibility failures and missing loading, empty or error states as one page scored out of 10. Triggers on: 'does the UI look right', 'responsive check', 'a11y check', 'UX audit', or /hk-web-app-ui-validation."
allowed-tools: Bash, Read, Grep, Glob, Write, mcp__chrome-devtools__*
generated: true
---

# Web app UI validation

One Chrome walk of every route, at three widths, judging both what the screens look like and whether a visitor can get around them: layout, contrast and hierarchy, plus navigation, links, forms, keyboard and accessible names. A second lens reads the code of every component that fetches or submits and asks which of loading, empty, error and pending it handles, then confirms the worst gaps in the browser. Every finding shows the route, the element and the proof.

It changes no source file. On a site the person does not own it submits only the empty and invalid form attempts step 3 describes, never a valid one.

Scope: what the running UI does to a user, including the states it shows while data loads, is empty or fails. The error cell here reads the component; the path from catch to field is `debuggability-audit`'s UI lens; a native iOS or Android app is `hk-mobile-app-ui-validation`; charts are `hk-chart-validation`.

The page is built as `docs/skills/knowledge/hk-writeup/SKILL.md` steps 3 to 5 say, in `docs/skills/knowledge/hk-writeup/SHELL.html`, in the voice of `docs/playbook/tone_and_taste.md`. Read the voice doc before writing a sentence.

## Workflow

**1. Fix the subject and get it running.** Given a URL, use it. Given nothing, this workspace's frontend is the subject: start it with the workspace's dev command (CLAUDE.md) as a background command, and wait for it to answer. In another repo with no URL, ask for the URL in one chat line and stop.

List the routes from the router or, for a site with no router in reach, from the navigation: open the start URL with `mcp__chrome-devtools__new_page` and `mcp__chrome-devtools__take_snapshot`, follow each link once and collect again, until no new page appears or you have 30. Routes that only redirect are skipped. For a route with a parameter (a detail page under a list), open the list route above it and click through to the first row; if the list is empty, skip the route and say so in the coverage table.

Done when you have a URL that answers and a route list. With no URL that answers, say so in one chat line and write no file.

**2. Walk every route at three widths.** Per route: `mcp__chrome-devtools__navigate_page`, `mcp__chrome-devtools__wait_for` on something the route renders, then for each of 1440, 834 and 390 wide `mcp__chrome-devtools__resize_page` and `mcp__chrome-devtools__take_screenshot` with `filePath` set to `writeup/shots/hk-web-app-ui-validation/<route>-<width>.png`. At 1440 also take `mcp__chrome-devtools__take_snapshot` for names and roles, press Tab through the page with `mcp__chrome-devtools__press_key` for focus order, and read `mcp__chrome-devtools__list_console_messages` and `mcp__chrome-devtools__list_network_requests`. Run `mcp__chrome-devtools__lighthouse_audit` once per distinct page template and keep its accessibility score and failures. Where a route needs data, open one that has some.

Done when every route has three PNGs on disk and a snapshot.

**3. Try every form.** Submit each form empty, then with one invalid field, using `mcp__chrome-devtools__fill_form` and `mcp__chrome-devtools__click`. Submit valid only on localhost or a URL the person named as a test instance; on any other host record the form as not submitted valid and say so on the page. Screenshot after each submit: did the form say what was wrong, next to which field, and what happened next?

**4. State lens: read the code, then confirm.** Skip it when no `.tsx`, `.jsx`, `.vue` or `.svelte` files are in reach and say so on the page. Otherwise follow "State lens" in [REFERENCE.md](REFERENCE.md): one row per fetching component with loading, empty, error and pending cells, the dead ends, and Chrome confirmation of every severity 5 and 4 candidate on localhost only. Done when every component has four cells and each severity 5 and 4 state finding says confirmed or read only.

**5. Run the checklist** in [REFERENCE.md](REFERENCE.md). Every check gets a yes or a no per route and width; group the noes by the defect behind them and carry each defect forward once, with the routes and widths it shows at.

**6. Point each finding at code.** Name the element from the snapshot, then grep the frontend for its class or text; carry the file and lines when one file owns the defect, the stylesheet when it is a shared style, and the URL plus element name for a remote site.

**7. Verify, then cut.** Reopen each cited screenshot and line and repeat the action once. Drop anything a maintainer would not change. Up to ten findings; fewer is fine, none is a finished audit that says the app is clean.

**8. Build the page** at `writeup/web-app-ui-validation-<subject>.html` at the repo root (`$(git rev-parse --show-toplevel)/writeup/`, gitignored; create the directory if missing), in the order below. Each finding embeds its PNG from `writeup/shots/hk-web-app-ui-validation/` as a base64 data URI (`take_screenshot` with the element's `uid` gives a crop when the full page is too much); then delete `writeup/shots/hk-web-app-ui-validation/` and nothing else.

**9. Report back** in one chat line: where the page went, the score, and the count by severity.

## Page order

1. Kicker "Web app UI validation", the subject as the title, chips for the URL, the widths and the model.
2. **Verdict**: one sentence with the score out of 10 and what set it (the band table in ../README.md), one naming the worst finding, then a `.tbl` of routes covered with the widths looked at and the Lighthouse accessibility score where run, and the state table from step 4 as a second `.tbl`.
3. **Findings**, one `section` per band: Blocks the user (severity 5 and 4), Slows the user (3), Nits (2 and 1). Each finding is one `details.row`: `.row-name` is the severity in words ("severity four"), `.row-status` the title as a sentence; the `.dbody` says what a user hits and what should have happened, shows the screenshot, and ends with route, width, element and `file:lines` in `.mono`.
4. `.foot`: replace the `.said` sentence with one saying how many routes were walked at which widths on the named model, which forms were not submitted valid, and that nothing was changed.

## Severity and score

Severity is 1 to 5, 5 worst: **5** when the user cannot complete what the screen is for — a dead link on the main path, a form that fails silently, text illegible at a width people use, data loss or a white screen; **4** when a silent failure or a double submit, a control cannot be reached by keyboard or has no accessible name, content is cut off the edge, or the grid breaks; **3** when the user can finish but has to work for it — inconsistent hierarchy, cramped controls, a table off the edge at tablet, an error shown far from its field; **2** for a padding or wording nit; **1** for a pixel gap or something only a linter would raise.

Score 0 to 10 and the band for each set of findings: [../README.md](../README.md).

## Closing step

Then the closing step in [../README.md](../README.md#closing-step): board rows for severity 4 and 5, a pattern for a recurring cause, one ledger line.

## Example

- The dev command is the one in your README; note the app and API ports.
- Name the router file.
- A parameterised route looks like `/items/:itemId`.
- For the state lens, name the API client folder, any component that calls `fetch(` directly, and the orchestration and worktree state folders to skip.
