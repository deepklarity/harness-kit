# Screenshot Proof Smoke Test

Manual E2E test for the screenshot upload and proof-with-visual-evidence flow. Each task instructs the agent to produce a visual artifact, capture it, and submit it as proof via the MCP tool.

## Prerequisites

- TaskIt backend running at `http://localhost:8000`
- MCP config generated in the working directory (`odin mcp-setup`)
- TaskIt UI open in browser at `http://localhost:5173`
- `media/` directory writable by the backend process

## Tasks

### Task 1: Generate and screenshot an HTML page

Create a file `smoke_output/colors.html` — a single HTML page showing a 3×3 grid of colored boxes with hex labels. Use inline CSS. After creating the file, take a screenshot of it using a shell command (e.g., `screencapture` on macOS or any available screenshot tool). Submit proof with `comment_type="proof"` and include the screenshot via `screenshot_paths`.

If no screenshot tool is available, create a 1×1 pixel PNG file as a stand-in and submit that instead. The goal is to exercise the upload flow.

**Expected behavior:**
1. Agent creates the HTML file
2. Agent captures or creates an image file
3. Agent calls `taskit_add_comment(comment_type="proof", content="...", screenshot_paths=["/path/to/screenshot.png"])`
4. Screenshot appears as an inline image in the proof comment on the TaskIt UI

### Task 2: Terminal output capture

Write a Python script `smoke_output/fib.py` that prints the first 20 Fibonacci numbers. Run the script and capture the terminal output to a text file. Then create a simple SVG or PNG visualization of the sequence (a bar chart or line — keep it simple, inline SVG rendered to a file is fine). Submit proof with both the script output description and the visualization as a screenshot.

**Expected behavior:**
1. Agent writes and runs the script
2. Agent creates a visual artifact (SVG/PNG)
3. Agent submits proof with `screenshot_paths` pointing to the image
4. Image renders inline in the proof comment

### Task 3: Proof without screenshots (regression check)

Write a haiku to `smoke_output/haiku.txt`. Submit proof as text only — no `screenshot_paths`. This verifies the existing text-only proof flow still works unchanged.

**Expected behavior:**
1. Agent writes the haiku
2. Agent submits proof via `taskit_add_comment(comment_type="proof", content="...")` with no `screenshot_paths`
3. Proof comment appears normally, with no image gallery section

## Verification Checklist

- [ ] Task 1: Screenshot uploads successfully (201 response)
- [ ] Task 1: Proof comment in TaskIt UI shows the image inline
- [ ] Task 1: Clicking the image opens it full-size in a new tab
- [ ] Task 1: Image filename appears below the thumbnail
- [ ] Task 2: Multiple screenshots can be uploaded in a single proof
- [ ] Task 2: `file_attachments` array is populated in the API response
- [ ] Task 3: Text-only proof still works (no regression)
- [ ] Task 3: `file_attachments` is an empty array (not missing)
- [ ] Media files are stored under `media/screenshots/YYYY/MM/`
- [ ] Task deletion cascades to delete associated attachments
- [ ] Large files (>10 MB) are rejected with 400

## Notes on Screenshot Capture

CLI agents don't have built-in screenshot capabilities. The practical approaches for agents:

1. **Create image artifacts directly** — generate SVG, use PIL/Pillow to create PNG, render HTML to image via headless browser
2. **Shell tools** — `screencapture -x file.png` (macOS), `import -window root file.png` (Linux/X11), `wkhtmltoimage` for HTML→PNG
3. **Headless browser** — `playwright screenshot`, `puppeteer`, `chrome --headless --screenshot`
4. **Fallback** — create a minimal placeholder image to exercise the upload path

The `screenshot_paths` param accepts any image file — it doesn't require an actual screen capture. The name "screenshot" reflects the primary use case (visual proof of work), but any image works.
