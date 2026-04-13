# Session Stream Smoke Test

Minimal single-task spec used to smoke-test the live session streaming UI
in TaskIt. The task is intentionally tiny so the JSONL trace file is short
and the whole run finishes in under a minute.

## Tasks

1. **Session stream HTML** — Agent: claude. Create `smoke_output/session_stream.html` — a single HTML page with inline CSS (dark theme), an `<h1>` heading "Session Stream Smoke", a short paragraph describing what the page is for, and a small 3-row table with columns "Step", "Status", "Notes" filled with fictional values. Keep it under 60 lines total.
