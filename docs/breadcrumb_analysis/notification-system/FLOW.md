# Notification System

Trigger: backend event (status change, comment, task assign, planning complete, spec finished, question asked)
End state: bell badge updates, sound plays, desktop popup appears (if desktop enabled)

## Flow

### In-app notification (polling)

```
[backend event]
  → 6 trigger points (see below)

notification_signals.py :: notify_on_status_change() / notify_on_spec_finished()
  → fires on TaskHistory post_save signal
  → collects recipient_ids from BoardMembership

tasks/views.py :: TaskViewSet.assign() / .comments() / .question()
tasks/views.py :: SpecViewSet.planning_result()
  → explicit notify() calls after mutations

notification_service.py :: notify()
  → if AUTH_ENABLED=False: expands recipient_ids to all HUMAN/ADMIN users
  → filters to HUMAN/ADMIN roles only
  → excludes actor_email (no self-notification)
  → per-user: checks disabled_types + quiet_hours in NotificationPreference
  → Notification.objects.bulk_create(notifications)

tasks/models.py :: Notification (DB record created)

--- 10s poll cycle ---

NotificationContext.tsx :: pollNotifications()  [every 10s via usePolling]
  → GET /api/notifications/unread_count/

notification_views.py :: NotificationViewSet.unread_count()
  → _resolve_user(request) → returns first HUMAN/ADMIN when AUTH_ENABLED=False
  → returns {"count": N}

NotificationContext.tsx :: useEffect [unreadCount change]
  → if count increased from last baseline:
    [sound_enabled] → playNotificationSound('comment_added')  [notificationSound.ts]
    [desktop_enabled + Notification.permission=granted] → new Notification('TaskIt', ...)

NotificationBell.tsx :: render
  → shows unreadCount badge (capped at "9+")
  → on popover open → fetchNotifications() → GET /api/notifications/
  → renders NotificationDropdown
```

## Trigger points summary

| Event | Location | Recipients |
|-------|----------|------------|
| Task status changed | `notification_signals.py:52` | All board members |
| Spec all tasks terminal | `notification_signals.py:124` | All board members |
| Task assigned to user | `views.py:1592` | Assignee only |
| Comment added | `views.py:1713` | All board members |
| Question asked | `views.py:1790` | All board members |
| Planning complete/failed | `views.py:2485` | All board members |
