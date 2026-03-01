# Taskit Frontend

React + TypeScript + Vite dashboard for Taskit task orchestration.

## Prerequisites

- Node.js (LTS recommended)
- npm
- Python 3 (only needed for `populate_data.py`)
- Taskit backend running at `http://localhost:8000` (or your configured backend URL)

All commands below assume you are in `taskit/taskit-frontend/`.

## Step-by-Step Setup

1. Install dependencies:

```bash
npm install
```

2. Configure environment:

```bash
cp .env.example .env
```

3. Start development server:

```bash
npm run dev
```

Frontend runs on `http://localhost:5173` by default.

## Environment Variables

Use `.env.example` as the base.

### Required

```dotenv
VITE_HARNESS_TIME_API_URL=http://localhost:8000
```

### Optional (polling)

```dotenv
VITE_POLL_INTERVAL_MS=15000
```

### Optional (Taskit auth mode)

Only needed when backend auth is enabled:

```dotenv
VITE_AUTH_ENABLED=true
```

## Run the Frontend

```bash
npm run dev
```

## Build, Lint, and Test

```bash
npm run build      # TypeScript type-check + Vite production build
npm run lint       # ESLint
npm run preview    # Preview production build
npm run test       # Vitest (watch mode)
npm run test:run   # Vitest (single run)
```

## Populate Demo Data

Requires backend API to be running.

```bash
python populate_data.py
```

## Quick Verification

1. Backend is healthy:
   - `http://localhost:8000/health/`
2. Frontend dev server is up:
   - `http://localhost:5173`
3. Dashboard loads tasks without API errors in browser dev tools.

## Architecture Overview

### State Management

`App.tsx` is the root state owner. It holds dashboard data, view mode, selected board/task, and modal visibility.

### Service Layer

- **`IntegrationService`** (`services/integration/`): abstract interface for data integrations.
- **`HarnessTimeService`** (`services/harness/`): Taskit backend API implementation, including data transformation and time-in-status calculations from mutation history.

### Components

| Component | Purpose |
|---|---|
| `KPICards` | KPI metrics (tasks, members, completion, in-progress, average time, mutations) |
| `TaskCard` / `TaskList` | Task presentation in list/grid |
| `TimelineView` | Chronological activity feed |
| `TaskDetailModal` | Task details, history, inline editing, assignee selection |
| `MemberCards` | Per-member analytics |
| `CreateTaskModal` | Task creation |
| `CreateBoardModal` | Board creation |
| `DashboardCharts` | Bar/pie/line charts via Recharts |
| `CountdownTimer` | ETA countdown display |

### Types

Core interfaces in `types/index.ts`: `Member`, `Task`, `TaskMutation`, `Board`, `DashboardData`, `DashboardStats`.

### Styling

The app uses Tailwind CSS (via Vite plugin) with project-level styles in `src/index.css`, including dark mode and design tokens.

## Troubleshooting

- **Frontend cannot reach backend**
  - Verify `VITE_HARNESS_TIME_API_URL` in `.env`.
  - Confirm backend is running on the configured URL.
- **Blank dashboard or fetch errors**
  - Check browser network tab for failed API calls.
  - Confirm backend `/health/` responds.
- **Auth issues**
  - Ensure `VITE_AUTH_ENABLED` matches backend `AUTH_ENABLED`.
  - Confirm backend CORS/auth cookie settings allow `http://localhost:5173`.
- **Changes to `.env` not applied**
  - Restart `npm run dev` after editing env variables.

## Project Links

- Backend README: [../taskit-backend/README_BACKEND.md](../taskit-backend/README_BACKEND.md)
- Backend API docs: [../taskit-backend/docs/API.md](../taskit-backend/docs/API.md)
- Taskit overview: [../README.md](../README.md)
- Monorepo root: [../../README.md](../../README.md)
- Contributing: [../../CONTRIBUTING.md](../../CONTRIBUTING.md)
- Security: [../../SECURITY.md](../../SECURITY.md)
