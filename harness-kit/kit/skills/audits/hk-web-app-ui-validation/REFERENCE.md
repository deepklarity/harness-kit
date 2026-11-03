# Web app UI validation: reference

The state lens (step 4) and the checklist (step 5) of [SKILL.md](SKILL.md).

## State lens: read the code, then confirm

Skip this step when there are no `.tsx`, `.jsx`, `.vue` or `.svelte` files in reach (a remote site) and say so on the page. Otherwise grep the frontend for `fetch(`, `axios`, `useQuery`, `useMutation` and imports of the project's API client, and list every component that fetches or submits. Fill one row per component with four cells, loading, empty, error and pending submit, each yes, no or n/a with the line that proves it; split the reading across haiku subagents by directory when the list passes 30. Then walk the dead ends: a delete with no confirmation or undo, a modal that closes without saving, an unknown route that shows a blank, an action with no success signal. Confirm every severity 5 and 4 candidate in Chrome on localhost only: `mcp__chrome-devtools__emulate` with `networkConditions` "Slow 3G" for the loading state, a list with nothing in it for the empty state, "Offline" plus the action for the error state, then omit `networkConditions` to restore; screenshot each into `writeup/shots/hk-web-app-ui-validation/`. Never take down a backend other people use. Done when every component has four cells and each severity 5 and 4 state finding says confirmed or read only.

## The checklist

Per route, answer each:

- Nothing overflows the viewport sideways, and nothing is clipped or overlaps another element at 1440, 834 or 390.
- At 834 and 390 the layout reflows: columns stack, tables scroll inside their container, the navigation collapses into something reachable.
- The one thing the screen wants the user to do is visually first, and it is reachable in one click from the navigation; the way back is obvious.
- The user knows where they are: a page title, one `h1`, and a current-place marker in the navigation.
- Every link goes somewhere that answers 200 and matches its text; a link that says "Settings" lands on settings.
- Body text is at least 14px, headings step down in the order they appear, and one visual hierarchy runs through the screen.
- Contrast passes 4.5:1 for text and 3:1 for large text and controls (measure with `mcp__chrome-devtools__evaluate_script` reading computed colours when the screenshot is unclear); Lighthouse reports no accessibility failure you cannot explain.
- Tab order follows reading order, focus is visible on every stop, nothing traps focus, and every control works from the keyboard alone.
- Every image, icon button and form field has an accessible name in the snapshot, and roles are right: a button is a button, not a div with a click handler.
- A form says, next to the field, what is wrong and how to fix it; submit is ignored while a request is in flight; success is confirmed on screen.
- Spacing repeats a scale, and the same component looks the same everywhere it appears.
- The console carries no error and the network shows no failed asset, 4xx or 5xx.
- While data loads the screen says so where the data will appear; an empty list says so and offers the next step; a failed request shows a message where the user was looking and a retry, and nothing throws to a white screen.
- While a submit is in flight the button is disabled or shows progress and a second click sends nothing; after it, the user is told it worked and the screen shows the change without a reload.
- A destructive action asks first or can be undone; an unknown route or missing id shows a page that says so and links back.
