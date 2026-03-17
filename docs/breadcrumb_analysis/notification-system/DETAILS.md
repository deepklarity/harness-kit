# Notification System — Detailed Trace

## 1. Signal-based triggers

**File**: `tasks/notification_signals.py`
**Registered in**: `tasks/apps.py` (imported in `TasksConfig.ready()`)

### notify_on_status_change (lines 17–67)
**Trigger**: `post_save` on `TaskHistory` where `field_name == "status"` and `created=True`
**Calls**: `notification_service.notify()`

Key logic:
- Guards: `not created` → return; `field_name != "status"` → return
- `recipient_ids` = `BoardMembership.objects.filter(board=task.board).values_list("user_id", flat=True)`
- Empty board → logs debug, returns early
- `actor_email` = `instance.changed_by` (the email of whoever triggered the status change)
- title pattern: `'Task "{task.title}" → {new_value}'`

### notify_on_spec_finished (lines 70–138)
**Trigger**: `post_save` on `TaskHistory`, status change to `DONE` or `FAILED`, AND all sibling tasks in spec are also terminal

Key logic:
- Guards: same as above + `new_value.upper() not in {"DONE","FAILED"}` → return
- Fetches all sibling task statuses: `Task.objects.filter(spec=task.spec).values_list("status", flat=True)`
- `not all(s.upper() in _TERMINAL_STATUSES for s in sibling_statuses)` → return (waits for all)
- Fires once per spec completion (when the last task crosses terminal)

---

## 2. View-based triggers

**File**: `tasks/views.py`

### TaskViewSet.assign (line ~1592)
- `recipient_ids = [assignee.id]` — targeted, not board-wide
- `actor_email` = `ser.validated_data["updated_by"]`

### TaskViewSet.comments POST (line ~1713)
- `recipient_ids` = all board members
- `body` = `comment.content[:200]`
- `actor_email` = `comment.author_email`

### TaskViewSet.question (line ~1790)
- `recipient_ids` = all board members
- `body` = `ser.validated_data["content"][:200]`
- Also sets `task.metadata["has_pending_question"] = True`

### SpecViewSet.planning_result (line ~2485)
- `recipient_ids` = all board members of `spec.board`
- `verb` = `"completed"` or `"failed"` depending on result
- `actor_email` = `f"{agent}+{model}@odin.agent"`

---

## 3. notification_service.notify()

**File**: `tasks/notification_service.py` lines 39–122

Data in: `recipient_ids: list`, `notification_type: str`, `title`, `body`, optional `task/spec/board`, `actor_email`

Key logic:
1. **Dev-mode expansion** (lines ~64–74): if `AUTH_ENABLED=False`, unions `recipient_ids` with all HUMAN/ADMIN user IDs — ensures admin receives all notifications without board membership
2. **Role filter**: `User.objects.filter(id__in=recipient_ids, role__in=[HUMAN, ADMIN])`
3. **Actor exclusion**: `recipients.exclude(email__iexact=actor_email_lower)`
4. **Per-user preference loop**:
   - `NotificationPreference.objects.get_or_create(user=user)` — creates default prefs if none exist
   - `notification_type in pref.disabled_types` → skip
   - `_is_quiet_hours(pref)` → skip (timezone-aware, handles midnight wrap)
5. **Bulk create**: `Notification.objects.bulk_create(notifications)`

Data out: `None` — side effects only (DB records)

---

## 4. Notification API views

**File**: `tasks/notification_views.py`

### _resolve_user(request) (lines 15–44)
Resolution order:
1. `request.taskit_user` — set by `TaskitAuthMiddleware` when `AUTH_ENABLED=True`
2. `?user_id=` query param
3. `?email=` query param
4. `User.objects.filter(role__in=[HUMAN, ADMIN]).order_by("id").first()` — dev fallback

Returns `None` if all fail → views return `{"detail": "Could not resolve user."}` 400.

### NotificationViewSet.get_queryset() (lines ~47–73)
- Resolves user, filters `Notification.objects.filter(recipient=user)`
- Optional filters: `?is_read=true/false`, `?type=<NotificationType>`, `?since=<ISO8601>`
- Ordered by `-created_at`

### Endpoints
- `GET /api/notifications/` — list notifications
- `GET /api/notifications/{id}/` — retrieve one
- `POST /api/notifications/{id}/read/` — mark as read
- `POST /api/notifications/mark_all_read/` — mark all unread as read
- `GET /api/notifications/unread_count/` — returns `{"count": N}`
- `GET /api/notifications/preferences/` — fetch user's preferences
- `PUT /api/notifications/preferences/` — update preferences

---

## 5. Frontend polling (NotificationContext.tsx)

**Poll**: `usePolling(pollNotifications, {enabled: true, intervalMs: 10_000, immediate: true})`
- Pauses automatically when tab is hidden (`document.hidden`)
- Exponential backoff on error (doubles each failure, capped at 120s)

**Side effects on unread count increase** (lines 90–122):
- `lastSeenCountRef` baseline — first observation sets baseline without alerting
- Subsequent increase → sound + desktop notification
- Sound: always `playNotificationSound('comment_added')` regardless of notification type (uses `/sounds/abstract-sound3.wav`)
- Desktop: `new Notification('TaskIt', {body: "You have N unread notifications."})` — generic, not per-event

**Bell opens** → `fetchNotifications()` → `GET /api/notifications/` → sets `notifications` state

---

## 6. usePolling hook

**File**: `src/hooks/usePolling.ts`

- Backoff formula: `Math.min(intervalMs * (2 ** retryCount), maxRetryDelayMs)`
- `maxRetryDelayMs` default: 120,000ms
- Visibility: `document.addEventListener('visibilitychange')` — pauses when hidden, resumes on focus
- Exposes `refreshNow()` for manual trigger

---

## 7. NotificationPreference defaults

Created via `get_or_create` on first notify() or GET preferences. Defaults:
- `desktop_enabled = True`
- `sound_enabled = True`
- `disabled_types = []`
- `quiet_hours_start = None` (disabled)
- `quiet_hours_timezone = "UTC"`
