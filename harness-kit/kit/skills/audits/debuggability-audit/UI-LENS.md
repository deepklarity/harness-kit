# UI lens: caught, then stored, then shown

Every fallible call (a provider CLI, a background task, an external request, a database write,
a parse) must be trapped, written to a field the API returns (a status or error field), and
read by a component that renders the failure before any empty or clean state. A missing link
is a finding. A fallback that turns a crash into a plausible-looking result is the worst form.

Signals; each hit is a candidate, and a finding is one broken link you followed from the catch
through the stored field, the serializer, the API payload, the component and its render branch:

- **No handling.** A provider call, subprocess, HTTP request or parse with no `try`; one bad
  response 500s the request or kills the task.
- **Caught, only logged.** An `except` or `catch` whose body only logs, prints or passes and
  never sets a status or error field.
- **Stuck pending.** A row created as pending or running and handed to a background worker
  with no path to failed when the worker never runs or times out. Check for a watchdog or
  reconciler before flagging.
- **Stored, not serialized.** A status or error field the serializer does not return.
- **Empty state over a failed run.** An empty list rendered as "No ...", "Passed" or a dash
  without first branching on the failed status or error field.
- **Silent catch in the UI.** `.catch(() => {})`, a `catch` that only calls `console.error`,
  or an awaited call in a handler with no visible error on the failure path.
- **Stored, never read.** An error or status field in the frontend types that no component
  references (grep the whole frontend before saying so).
- **Raw error on screen.** `JSON.stringify(err)`, `[object Object]` or a raw `{"detail": ...}`
  body instead of a sentence.
- **Poll with no end.** A poll or interval that refreshes while pending and never says "this is
  taking too long".
- **Header says failed, body says clean.** A summary line that can render "Failed" while the
  detail below still shows "no issues".

Clear what holds: a failure already shown as a toast, a best-effort catch that persists and is
rendered. Confirm an empty branch can render for a failed run before calling it silent.

UI-lens severity, 1 to 5: **5** the person sees success or a clean state for a run that
failed; **4** the person sees nothing, or a spinner that never ends; **3** the error is stored
and returned but no component reads it, or header and body disagree; **2** the error shows in
the wrong place or as a generic message when a specific one exists; **1** the error shows and
only its wording could be better. Each finding gives the severity, `file:line` at the swallow
point, what the person sees instead, and the link to add with the sentence the person should
see.
