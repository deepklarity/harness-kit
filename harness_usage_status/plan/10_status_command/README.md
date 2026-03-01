# 10 — Status Command

## Goal
Implement the main `status` CLI command that queries all providers and displays a unified summary.

## Tasks
- [ ] Add `status` subcommand to CLI in `cli.py`
- [ ] Query all enabled providers in parallel using `asyncio.gather()`
- [ ] Render rich table with columns:
  - Provider name
  - Plan/tier
  - Quota remaining
  - Usage %
  - Status (online/offline)
  - Reset date
- [ ] Add `--json` flag for machine-readable JSON output
- [ ] Add `--provider` flag to query a single provider
- [ ] Handle provider errors gracefully (show error in table row, don't crash)

## Acceptance Criteria
- `harness-usage-status status` shows a formatted table for all configured providers
- `harness-usage-status status --json` outputs valid JSON
- Failed providers show error status, other providers still display
