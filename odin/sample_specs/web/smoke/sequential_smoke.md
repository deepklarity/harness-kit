# Sequential Smoke Test

A 4-task sequential chain for testing dependency resolution and task execution flow. Each task is trivially small (minimal tokens) but depends on the previous one, so they must execute one-by-one in order.

## Tasks

1. **Init manifest** — Agent: gemini. Create `chain/manifest.json` with this exact content: `{"steps": [], "status": "started"}`. Nothing else.
2. **Step A** — Agent: gemini. Depends on: 1. Read `chain/manifest.json`, append `"step_a"` to the `steps` array, set `status` to `"step_a_done"`, and write it back.
3. **Step B** — Agent: gemini. Depends on: 2. Read `chain/manifest.json`, append `"step_b"` to the `steps` array, set `status` to `"step_b_done"`, and write it back.
4. **Finalize** — Agent: gemini. Depends on: 3. Read `chain/manifest.json`, append `"finalize"` to the `steps` array, set `status` to `"complete"`, write it back. Then create `chain/done.txt` containing the final JSON from `manifest.json`.
