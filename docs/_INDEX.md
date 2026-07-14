# docs/

Master index for all documentation in the harness-kit monorepo.

## Where things go

| Type | Location | Purpose | Created by |
|------|----------|---------|------------|
| **Breadcrumbs** | `breadcrumb_analysis/` | End-to-end flow traces (FLOW + DETAILS + DEBUG) | `/hk-breadcrumb-creator` |
| **Solutions** | `solutions/<category>/` | Compounded learnings (legacy — new entries land in `patterns/` via `/hk-compound`) | Manual |
| **Philosophy** | `philosophy/` | Core design principles that govern how we build | Manual |
| **Testing process** | `testing_process/` | Testing framework, RCA protocol, test categories | Manual |
| **Guides** | `guides/` | Operational how-tos — deployment, building MCPs | Manual |

## Sub-indexes

- `breadcrumb_analysis/_INDEX.md` — all flow traces + symptom quick-nav
- `solutions/` — organized by category (currently `workflow-issues/`)

## Current contents

### breadcrumb_analysis/
20 flow traces covering Odin lifecycle + supporting systems. See `breadcrumb_analysis/_INDEX.md` for full list and symptom quick-nav.

### solutions/
2 compounded learnings. Browse by directory.

### philosophy/
- `testing.md` — single source of truth for derived data, what to test where

### testing_process/
- `testcase_process_and_philosophy.md` — full testing framework, TDD wave pattern, RCA protocol
- `testing_end_to_end.md` — test layers (unit/mock/snapshot/e2e), diagnostic scripts, snapshot workflow

### guides/
- `deployment.md` — deploying taskit-backend, taskit-frontend, odin across environments
- `forkd-setup.md` — configuring forkd microVM sandbox execution for Odin agents
- `mcp_building.md` — creating MCP servers in Python (FastMCP) or Node/TypeScript
