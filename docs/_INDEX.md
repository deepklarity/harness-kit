# docs/

Master index for all documentation in the harness-kit monorepo.

## Where things go

| Type | Location | Purpose | Created by |
|------|----------|---------|------------|
| **Breadcrumbs** | `breadcrumb_analysis/` | End-to-end flow traces (FLOW + DETAILS + DEBUG) | `/dk-breadcrumb-creator` |
| **Solutions** | `solutions/<category>/` | Compounded learnings — fixes, patterns, anti-patterns | `/dk-compound` |
| **Philosophy** | `philosophy/` | Core design principles that govern how we build | Manual |
| **Testing process** | `testing_process/` | Testing framework, RCA protocol, test categories | Manual |
| **Guides** | `guides/` | Operational how-tos — deployment, building MCPs | Manual |
| **Brainstorms** | `brainstorms/` | Timestamped design explorations for future work | Manual |
| **Prompts** | `prompts/` | Reusable prompt templates for audits, reviews, etc. | Manual |

## Sub-indexes

- `breadcrumb_analysis/_INDEX.md` — all flow traces + symptom quick-nav
- `solutions/` — organized by category: `architecture/`, `design-patterns/`, `ui-bugs/`, `performance-issues/`, `best-practices/`, `patterns/`

## Current contents

### breadcrumb_analysis/
8 flow traces covering Odin lifecycle + supporting systems. See `breadcrumb_analysis/_INDEX.md` for full list and symptom quick-nav.

### solutions/
12 compounded learnings across 6 categories. Browse by directory.

### philosophy/
- `testing.md` — single source of truth for derived data, what to test where
- `cost_logging.md` — token usage flow and cost estimation architecture (compute once at backend, display everywhere)

### testing_process/
- `testcase_process_and_philosophy.md` — full testing framework, TDD wave pattern, RCA protocol
- `testing_end_to_end.md` — test layers (unit/mock/snapshot/e2e), diagnostic scripts, snapshot workflow

### guides/
- `deployment.md` — deploying taskit-backend, taskit-frontend, odin across environments
- `mcp_building.md` — creating MCP servers in Python (FastMCP) or Node/TypeScript

### brainstorms/
- `2026-02-19-sandboxed-execution-brainstorm.md` — persistent sandbox pool with process isolation
- `2026-02-20-dag-executor-brainstorm.md` — DAG executor with EXECUTING status, Celery integration
- `2026-02-20-traceability-debuggability-brainstorm.md` — traceability and debuggability features

### prompts/
- `slop_audit_prompt.md` — copy-paste prompt for codebase hygiene audits
