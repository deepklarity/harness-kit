# DAG 3-Wave Dependency Chain Smoke Test

Tests that wave ordering is correct with a 3-wave dependency chain.

Wave 1: Tasks 1 and 2 run in parallel (no deps).
Wave 2: Task 3 depends on task 1; task 4 depends on task 2. Both can run in parallel.
Wave 3: Task 5 depends on tasks 3 and 4 (waits for both).

## Tasks

1. **Wave1-A** — Agent: claude. Write `wave1/a.txt` with content: `wave1-a-done`
2. **Wave1-B** — Agent: claude. Write `wave1/b.txt` with content: `wave1-b-done`
3. **Wave2-A** — Agent: claude. Depends on: 1. Read `wave1/a.txt` and write `wave2/a.txt` with content: `wave2-a-done (saw wave1-a)`
4. **Wave2-B** — Agent: claude. Depends on: 2. Read `wave1/b.txt` and write `wave2/b.txt` with content: `wave2-b-done (saw wave1-b)`
5. **Wave3** — Agent: claude. Depends on: 3, 4. Read `wave2/a.txt` and `wave2/b.txt`, write `wave3/done.txt` with content: `wave3-done (saw wave2-a and wave2-b)`
