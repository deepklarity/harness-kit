# Quickstart Sample: Add a --verbose flag

Add a `--verbose` / `-v` CLI flag to `odin/sample_specs/quickstart/toy.py`.
When the flag is passed, the script prints each step to stderr as it runs
(`[step] load`, `[step] process`, ...). Without the flag, output is unchanged.

Use whatever provider is available on this machine — the loader
(`run.py`) passes `--base-agent` for you based on `odin doctor`. Keep the
change tiny and self-contained; use argparse.

## Tasks

1. **Add --verbose flag** — In `odin/sample_specs/quickstart/toy.py`, add a
   `-v` / `--verbose` flag via argparse. When set, print `[step] <name>` to
   stderr for each step before it runs. Verify by running
   `python3 odin/sample_specs/quickstart/toy.py --verbose` and confirming the
   step lines appear on stderr while `done: <name>` still appears on stdout.
