# Notification System — Debug Guide

## Log locations

| Layer | File | What's in it |
|-------|------|-------------|
| Django app | `taskit/taskit-backend/logs/taskit_detail.log` | Signal fires, notify() calls, preference skips |
| Django app | `taskit/taskit-backend/logs/taskit.log` | Abbreviated — no tracebacks |
| Frontend | browser console | API errors, desktop notification failures |

## What to search for

| Symptom | Where to look | Search term |
|---------|--------------|-------------|
| Signal fired but no notification created | `taskit_detail.log` | `notification_signals` |
| notify() called, eligible_recipients empty | `taskit_detail.log` | `No board members` |
| resolve_user returned None (400 on API) | browser network tab | `Could not resolve user` |
| Bell count not updating | browser console | `fetchUnreadCount` error |

## Quick commands

```bash
# Check if any notifications exist in DB
cd taskit/taskit-backend && .venv/bin/python -c "
import django, os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup()
from tasks.models import Notification
print(f'Total: {Notification.objects.count()}')
for n in Notification.objects.select_related('recipient').order_by('-created_at')[:10]:
    print(f'  id={n.id} type={n.notification_type} recipient={n.recipient.email} read={n.is_read}')
"

# Check which users will receive notifications (board members + HUMAN/ADMIN)
cd taskit/taskit-backend && .venv/bin/python -c "
import django, os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup()
from tasks.models import User, BoardMembership, UserRole
humans = User.objects.filter(role__in=[UserRole.HUMAN, UserRole.ADMIN])
print('HUMAN/ADMIN users (always notified when AUTH_ENABLED=False):')
for u in humans: print(f'  {u.email} role={u.role}')
print()
for bm in BoardMembership.objects.select_related('user','board'):
    print(f'board={bm.board.name} member={bm.user.email} role={bm.user.role}')
"

# Check notification preferences
cd taskit/taskit-backend && .venv/bin/python -c "
import django, os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup()
from tasks.models import NotificationPreference
for p in NotificationPreference.objects.select_related('user').all():
    print(f'user={p.user.email} desktop={p.desktop_enabled} sound={p.sound_enabled} disabled={p.disabled_types} quiet={p.quiet_hours_start}-{p.quiet_hours_end}')
"

# Manually fire a test notification
cd taskit/taskit-backend && .venv/bin/python -c "
import django, os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup()
from tasks.models import User, UserRole
from tasks.notification_service import notify
admin = User.objects.filter(role__in=[UserRole.HUMAN, UserRole.ADMIN]).first()
notify(recipient_ids=[admin.id], notification_type='comment_added', title='Test notification', body='Fired manually from shell')
print('Done — check Notification.objects.all()')
"

# Tail the backend log for notification activity
tail -f taskit/taskit-backend/logs/taskit_detail.log | grep -i notification
```

## Env vars that affect this flow

| Variable | Effect | Default |
|----------|--------|---------|
| `AUTH_ENABLED` | When False, all HUMAN/ADMIN users receive all notifications regardless of board membership | `False` |

## Common breakpoints

- `notification_service.py:notify()` line ~85 — inspect `eligible_recipients` list; if empty, no notifications created
- `notification_signals.py:notify_on_status_change()` line ~41 — inspect `recipient_ids`; if empty list, board has no members
- `notification_views.py:_resolve_user()` line ~26 — check what user is resolved; None → 400 on all notification endpoints
- `NotificationContext.tsx:pollNotifications()` — add console.log to see if poll is firing and what count it returns

## Failure modes

**No notifications appearing at all**
1. Check: is there a HUMAN or ADMIN user? (`User.objects.filter(role__in=[HUMAN,ADMIN])`)
2. Check: `AUTH_ENABLED=False`? If yes, step 1 covers it. If `AUTH_ENABLED=True`, check board membership.
3. Check: are events actually firing? Trigger a status change and grep logs for `notification_signals`.

**Bell badge stuck at 0**
1. Open browser network tab, look for `GET /api/notifications/unread_count/`
2. If 400 → `_resolve_user()` returned None → no HUMAN/ADMIN users in DB
3. If no request at all → `usePolling` may be paused (tab was hidden on load)

**Sound not playing**
1. Check browser console for audio play errors (autoplay policy blocks audio without user gesture)
2. Check: does `/sounds/abstract-sound3.wav` return 200? (Network tab)
3. Check: `preferences.sound_enabled` is true (GET `/api/notifications/preferences/`)

**Desktop popup not appearing**
1. Check: `Notification.permission` in browser console — must be `"granted"`
2. Check: `preferences.desktop_enabled` is true
3. The popup fires only when unread count *increases* — if count was already N, opening the page won't trigger it
