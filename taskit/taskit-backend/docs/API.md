# API Reference

Base URL: `http://localhost:8000` (no path prefix — endpoints are at the root)

## Health

| Method | URL | Description | Status |
|--------|-----|-------------|--------|
| GET | `/health/` | Health check | 200 |

## Common List Query Contract

Supported on paginated module lists:

- `page`, `page_size` (default `25`, max `200`)
- `search` (alias `q` on frontend)
- `sort` (comma-separated; `-` prefix for descending), e.g. `sort=-created_at,title`
- `created_from`, `created_to`
- `updated_from`, `updated_to`

Date params accept ISO date (`YYYY-MM-DD`) or ISO datetime.

## Users 

| Method | URL | Body | Status |
|--------|-----|------|--------|
| POST | `/users/` | `name`, `email` | 201 |
| GET | `/users/` | — | 200 |
| GET | `/users/:id/` | — | 200 |
| PUT | `/users/:id/` | `name`, `email` | 200 |
| DELETE | `/users/:id/` | — | 204 |


- `board_id` (alias `board`)
- `role` (`HUMAN`, `AGENT`, `ADMIN`)
- `joined_from`, `joined_to` (mapped to `users.created_at`)

Member sort fields:

- `name`, `created_at`, `task_count`

## Boards

| Method | URL |
|--------|-----|
| GET | `/boards/` |
| GET | `/boards/:id/` |
| POST | `/boards/` |
| PUT | `/boards/:id/` |
| DELETE | `/boards/:id/` |
| POST | `/boards/:id/clear/` |
| GET | `/api/boards/` |

Board sort fields:

- `name`, `created_at`, `updated_at`, `member_count`

## Tasks

| Method | URL |
|--------|-----|
| GET | `/tasks/` |
| GET | `/tasks/:id/` |
| POST | `/tasks/` |
| PUT | `/tasks/:id/` |
| DELETE | `/tasks/:id/` |
| GET | `/tasks/:id/detail/` |
| GET | `/tasks/:id/history/` |
| GET/POST | `/tasks/:id/comments/` |
| POST | `/tasks/:id/question/` |
| POST | `/tasks/:id/comments/:comment_id/reply/` |
| POST | `/tasks/:id/assign/` |
| POST | `/tasks/:id/unassign/` |
| POST | `/tasks/:id/labels/` |
| DELETE | `/tasks/:id/labels/` |
| POST | `/tasks/:id/execution_result/` |
| GET | `/api/tasks/` |

Task filters:

- `board_id` (alias `board`)
- `status`
- `assignee_id` (alias `assignee`)
- `priority`
- `spec_id` (alias `spec`)
- `label_ids` (aliases: `labels`, `label`)

Task sort fields:

- `created_at`, `title`, `priority`, `status`, `due_date`

Task date fields:

- `start_date` (nullable)
- `due_date` (nullable)

## Specs

| Method | URL |
|--------|-----|
| GET | `/specs/` |
| GET | `/specs/:id/` |
| POST | `/specs/` |
| PUT | `/specs/:id/` |
| DELETE | `/specs/:id/` |
| GET | `/specs/:id/diagnostic/` |
| POST | `/specs/:id/clone/` |
| GET | `/api/specs/` |

Spec filters:

- `board_id` (alias `board`)
- `odin_id`
- `status` (`active`, `abandoned`)
- `abandoned` (`true/false`)

Spec sort fields:

- `created_at`, `title`, `task_count`

## Timeline and Kanban

| Method | URL | Notes |
|--------|-----|-------|
| GET | `/api/timeline/` | Unpaginated task + history feed |
| GET | `/api/kanban/` | Unpaginated card list, optional `board_id`, `date_from`, `date_to` |

Timeline filters:

- `board_id`, `status`, `assignee_id`, `priority`, `search`
- `date_from`, `date_to` (`created_at` date range)

Timeline sort fields:

| Method | URL | Body | Status |
|--------|-----|------|--------|
| POST | `/tasks/` | `board_id`, `title`, `description`?, `priority`?, `status`?, `created_by`, `assignee_id`?, `spec_id`?, `depends_on`?, `complexity`? | 201 |
| GET | `/tasks/` | — (query: `board_id`, `spec_id`) | 200 |
| GET | `/tasks/:id/` | — | 200 |
| PUT | `/tasks/:id/` | `title`?, `description`?, `dev_eta_seconds`?, `priority`?, `status`?, `assignee_id`?, `label_ids`?, `depends_on`?, `result`?, `complexity`?, `updated_by` | 200 |
| DELETE | `/tasks/:id/` | — | 204 |
| POST | `/tasks/:id/assign/` | `assignee_id`, `updated_by` | 200 |
| POST | `/tasks/:id/unassign/` | `updated_by` | 200 |
| POST | `/tasks/:id/labels/` | `label_ids`, `updated_by` | 200 |
| DELETE | `/tasks/:id/labels/` | `label_ids`, `updated_by` | 200 |
| GET | `/tasks/:id/history/` | — | 200 |
| GET | `/tasks/:id/comments/` | — (query: `after`, `type`) | 200 |
| POST | `/tasks/:id/comments/` | `author_email`, `author_label`?, `content`, `attachments`?, `comment_type`? | 201 |
| POST | `/tasks/:id/question/` | `author_email`, `author_label`?, `content` | 201 |
| POST | `/tasks/:id/comments/:comment_id/reply/` | `author_email`, `author_label`?, `content` | 201 |

## Notes

- All IDs are integers (auto-increment).
- Task priority: `LOW`, `MEDIUM` (default), `HIGH`, `CRITICAL`.
- Task status: `BACKLOG`, `TODO` (default), `IN_PROGRESS`, `EXECUTING`, `REVIEW`, `TESTING`, `DONE`, `FAILED`.
  - `EXECUTING` = agent actively running (set by DAG executor when dependencies are satisfied).
- `created_by` and `updated_by` are email strings.
- Every task mutation creates history records with old/new values.
- Comments are append-only (no edit/delete). `author_email` follows RFC 5321 plus-delimited format for agent identity: `{agent}+{model}@odin.agent` (e.g. `gemini+gemini-2.0@odin.agent`). Odin system comments use `odin@harness.kit`.
- **Question/Reply flow**: `POST /tasks/:id/question/` creates a comment with `attachments: [{"type": "question", "status": "pending"}]` and sets `task.metadata.has_pending_question = true`. `POST /tasks/:id/comments/:comment_id/reply/` creates a reply comment with `attachments: [{"type": "reply", "reply_to": <comment_id>}]`, marks the question as `"answered"`, and clears the pending flag.
- **Comment types** (`comment_type` field): `status_update` (default), `telemetry`, `question`, `reply`. The `question` and `reply` endpoints set this automatically. For regular comments, pass `comment_type` in the POST body to override the default. The `execution_result` endpoint creates comments with `comment_type: "telemetry"`.
- **Comment filtering**: `GET /tasks/:id/comments/?after=<comment_id>` returns only comments with `id > after` — used by polling clients to check for new replies. Combine with `?type=<comment_type>` to filter by type (e.g. `?type=question`, `?type=telemetry&after=5`).
- Odin integration: Tasks can optionally have `spec` (FK), `depends_on` (JSON list of task IDs), `result`, and `complexity` fields. Agent assignment uses the `assignee` FK — Odin agents are represented as User records (e.g. `name="claude", email="claude@odin.agent"`).
- Specs group related tasks under a planning unit. `odin_id` is the `sp_XXXX` identifier from Odin's planning phase.