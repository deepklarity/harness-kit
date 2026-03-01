# Taskit

Taskit is the task board and analytics module in this monorepo.
It provides the backend APIs and frontend dashboard for task lifecycle management, mutation history, timeline views, and operational visibility.

## Overview

Taskit contains two services:

- **Backend**: Django REST API for tasks, users, boards, history, and execution-related operations
- **Frontend**: React dashboard for task operations, timeline, and analytics views



## Service Documentation

- Backend setup, environment, run, and tests: [taskit-backend/README_BACKEND.md](taskit-backend/README_BACKEND.md)
- Frontend setup, environment, run, and tests: [taskit-frontend/README_FRONTEND.md](taskit-frontend/README_FRONTEND.md)

This README is intentionally overview-only and avoids duplicating service-level instructions.

## Developer Workflow

1. Start backend (`taskit-backend`)
2. Start frontend (`taskit-frontend`)
3. Optionally seed demo data (as documented in frontend README)

Use service READMEs for exact commands.

## Project Links

- Backend API reference: [taskit-backend/docs/API.md](taskit-backend/docs/API.md)
- Monorepo overview: [../README.md](../README.md)
- Contributing: [../CONTRIBUTING.md](../CONTRIBUTING.md)
- Security: [../SECURITY.md](../SECURITY.md)
