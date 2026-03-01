# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working in `taskit/`.

## What This Is

Taskit is a full-stack task orchestration dashboard: Django REST API backend + React/TypeScript/Vite frontend. It tracks task lifecycles with full mutation history, provides timeline views, team analytics, and KPI visualizations.

## Commands

### Backend (`taskit-backend/`)

```bash
pip install -r requirements.txt
python scripts/create_db.py          # Initialize PostgreSQL database
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

### Frontend (`taskit-frontend/`)

```bash
npm install
npm run dev       # Dev server on http://localhost:5173
npm run build     # TypeScript check + Vite build
npm run lint      # ESLint
npm run test      # Vitest (watch mode)
npm run test:run  # Vitest (single run)
```

### Populate demo data

```bash
cd taskit-frontend && python populate_data.py
```

## Architecture

### Backend

See `taskit-backend/CLAUDE.md` for detailed backend architecture, models, domain rules, API endpoints, and test commands.

### Frontend (React + Vite + TypeScript)

- **App.tsx**: Root state management — holds `DashboardData`, view mode, selected board/task, modal state.
- **Service layer** (`services/harness/HarnessTimeService.ts`): API client implementing `IntegrationService` interface. Fetches, transforms, and computes time-in-status from mutation history. Includes `parseActor()` for decoding agent identity emails (`{agent}+{model}@odin.agent`).
- **IntegrationService interface** (`services/integration/`): Abstraction layer so the data source can be swapped (Trello, Jira, etc.).
- **Types** (`types/index.ts`): `Member`, `Task`, `TaskComment`, `Board`, `TaskMutation`, `DashboardData`, `DashboardStats`, `Spec`, `SpecComment`, `SpecCostSummary`, `ReflectionReport`, `ModelInfo`, `CommentType`, `CommentFileAttachment`, `ProcessMonitorTask`, `AgentConfig`, `ViewMode`.
- **Pages** (`components/pages/`): BoardPage (kanban + timeline + DAG views per board), SpecsPage, MembersPage.
- **Components**: KPICards, KanbanBoard, TaskCard, TaskDetailModal, TimelineView, DagView, TraceViewer, SpecListView, SpecDetailView, SpecDebugView, SpecJourney, ReflectionModal, ReflectionListView, ReflectionDetailView, ReflectionReportViewer, MemberCards, MemberAvatarBar, MemberManagementPopover, CreateTaskModal, CreateBoardModal, CreateUserModal, EditUserModal, ManageMembersModal, ProcessMonitorModal, OdinGuideModal, DashboardCharts, CountdownTimer, TaskTimeDisplay, LoginPage, ChangePasswordPage, SettingsView, MarkdownEditor, MarkdownRenderer, FilterBar, SearchBar.
- **Utilities** (`utils/`): `transformer.ts` (status classification, formatting), `dagUtils.ts` (cycle detection, DAG layout with dagre, orphan separation, edge classification).
- **Contexts** (`contexts/`): AuthContext (Django JWT auth — access token in memory, refresh token via httponly cookie).
- **Testing**: Vitest + @testing-library/react + jsdom. Config in `vitest.config.ts`, setup in `src/test/setup.ts`. Tests live alongside source files (`*.test.ts`, `*.test.tsx`).
- **Styling**: Single `index.css` (dark mode, glassmorphism, CSS variables, animations). Tailwind v4.

### URL Parameters

- `?taskId=<id>` — Opens TaskDetailModal for the specified task (any route).
- `?view=dag` — Switches Timeline view to DAG dependency graph mode (`/timeline` only).

### Frontend Design Conventions

- **All view state must be URL-addressable.** Every distinct view mode, toggle, or sub-view should be backed by a URL parameter or route segment — never component-local `useState` alone. This ensures the view survives page reload, is shareable via link, and works with browser back/forward. Use `useSearchParams` for view toggles within a route (e.g., `?view=dag`) and dedicated routes for top-level views.
- **Detail sidebar vertical budget.** The TaskDetailModal sidebar (left 320px column) has a fixed space budget (~550px). New fields must be either (a) inlined with existing rows (Status+Priority pattern), (b) wrapped in `CollapsibleSection` if secondary, or (c) moved to the right column if > 2 lines. Destructive actions are pinned in `shrink-0` footers outside the scroll area. See `docs/solutions/design-patterns/sidebar-information-density-pattern.md`.
- **Never hide metrics when data is absent.** Metric cards (tokens, cost, duration, etc.) must always render with a placeholder value (`—`) when data is unavailable — never conditionally hidden. Hiding creates layout shifts, makes it unclear whether the metric exists, and prevents the user from knowing data is missing vs. the field not being relevant. The consistent card grid is part of the UI contract.

### Environment

Backend `.env`: `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `PORT`, `SECRET_KEY`, `DEBUG`, `AUTH_ENABLED` (default: `False`), `JWT_ACCESS_SECONDS`, `JWT_REFRESH_SECONDS`, `CORS_ALLOWED_ORIGINS`.
Frontend `.env`: `VITE_HARNESS_TIME_API_URL` (default: `http://localhost:8000`), `VITE_AUTH_ENABLED` (default: `false`).
