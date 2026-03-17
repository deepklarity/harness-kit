#!/usr/bin/env python3
"""Interactive notification system test script.

Run from taskit-backend/:
    USE_SQLITE=True python testing_tools/test_notifications.py

Tests every notification feature with visible output.
Requires the backend server to be running on localhost:8000.
"""
import json
import os
import sys
import time

# Ensure the backend root is on sys.path so `config.settings` resolves.
_backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_root not in sys.path:
    sys.path.insert(0, _backend_root)

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from tasks.models import (
    Board,
    BoardMembership,
    Notification,
    NotificationPreference,
    Task,
    TaskHistory,
    TaskStatus,
    User,
    UserRole,
)
from tasks.notification_service import notify

# ── Helpers ──────────────────────────────────────────────────────────────

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

_pass = 0
_fail = 0


def header(text):
    print(f"\n{BOLD}{CYAN}{'═' * 60}{RESET}")
    print(f"{BOLD}{CYAN}  {text}{RESET}")
    print(f"{BOLD}{CYAN}{'═' * 60}{RESET}")


def check(label, condition, detail=""):
    global _pass, _fail
    if condition:
        _pass += 1
        print(f"  {GREEN}✓{RESET} {label}")
    else:
        _fail += 1
        msg = f"  {RED}✗{RESET} {label}"
        if detail:
            msg += f"  — {RED}{detail}{RESET}"
        print(msg)


def info(text):
    print(f"  {YELLOW}→{RESET} {text}")


# ── Setup ────────────────────────────────────────────────────────────────

header("SETUP")

# Ensure a human user exists
human, created = User.objects.get_or_create(
    email="test-notif@taskit.local",
    defaults={"name": "Test Human", "role": UserRole.HUMAN},
)
if created:
    info(f"Created test user: {human.email} (id={human.id})")
else:
    info(f"Using existing test user: {human.email} (id={human.id})")

# Ensure a board exists and user is a member
board = Board.objects.first()
if not board:
    board = Board.objects.create(name="Test Board")
    info(f"Created board: {board.name}")

BoardMembership.objects.get_or_create(board=board, user=human)
info(f"User is member of board \"{board.name}\" (id={board.id})")

# Ensure a task exists
task = Task.objects.filter(board=board).first()
if not task:
    task = Task.objects.create(
        board=board,
        title="Test Task for Notifications",
        created_by=human.email,
    )
    info(f"Created task: \"{task.title}\" (id={task.id})")
else:
    info(f"Using existing task: \"{task.title}\" (id={task.id})")

# Clean up old test notifications
deleted, _ = Notification.objects.filter(
    recipient=human, title__startswith="[TEST]"
).delete()
if deleted:
    info(f"Cleaned up {deleted} old test notifications")


# ── Test 1: Direct notify() ─────────────────────────────────────────────

header("TEST 1: notify() creates Notification records")

before = Notification.objects.filter(recipient=human).count()

notify(
    recipient_ids=[human.id],
    notification_type="task_assigned",
    title="[TEST] You were assigned a task",
    body="This is a test notification.",
    task=task,
    board=board,
    actor_email="other-agent@odin.agent",
)

after = Notification.objects.filter(recipient=human).count()
check("Notification created", after == before + 1, f"before={before} after={after}")

n = Notification.objects.filter(recipient=human).order_by("-created_at").first()
check("Type is task_assigned", n.notification_type == "task_assigned")
check("Title matches", n.title == "[TEST] You were assigned a task")
check("Body matches", n.body == "This is a test notification.")
check("Task FK set", n.task_id == task.id)
check("Board FK set", n.board_id == board.id)
check("is_read defaults to False", n.is_read is False)


# ── Test 2: Self-notification exclusion ──────────────────────────────────

header("TEST 2: Actor is excluded (no self-notification)")

before = Notification.objects.filter(recipient=human).count()

notify(
    recipient_ids=[human.id],
    notification_type="comment_added",
    title="[TEST] Should not appear",
    body="Actor is the same as recipient.",
    task=task,
    board=board,
    actor_email=human.email,  # same as recipient
)

after = Notification.objects.filter(recipient=human).count()
check("No notification created (self-excluded)", after == before)


# ── Test 3: AGENT role excluded ──────────────────────────────────────────

header("TEST 3: AGENT role users are excluded")

agent = User.objects.filter(role=UserRole.AGENT).first()
if agent:
    before = Notification.objects.filter(recipient=agent).count()

    notify(
        recipient_ids=[agent.id],
        notification_type="status_changed",
        title="[TEST] Should not reach agent",
        board=board,
    )

    after = Notification.objects.filter(recipient=agent).count()
    check(f"No notification for agent {agent.email}", after == before)
else:
    info("No AGENT users found — skipping")


# ── Test 4: Disabled notification type ───────────────────────────────────

header("TEST 4: Disabled notification types are skipped")

pref, _ = NotificationPreference.objects.get_or_create(user=human)
old_disabled = pref.disabled_types
pref.disabled_types = ["comment_added"]
pref.save()

before = Notification.objects.filter(recipient=human).count()

notify(
    recipient_ids=[human.id],
    notification_type="comment_added",
    title="[TEST] Should be blocked by preference",
    board=board,
)

after = Notification.objects.filter(recipient=human).count()
check("Notification blocked (type disabled)", after == before)

# Restore
pref.disabled_types = old_disabled
pref.save()


# ── Test 5: Quiet hours ─────────────────────────────────────────────────

header("TEST 5: Quiet hours suppression")

pref, _ = NotificationPreference.objects.get_or_create(user=human)
old_start = pref.quiet_hours_start
old_end = pref.quiet_hours_end

# Set quiet hours to cover the entire day (00:00 - 23:59)
pref.quiet_hours_start = "00:00"
pref.quiet_hours_end = "23:59"
pref.quiet_hours_timezone = "UTC"
pref.save()

before = Notification.objects.filter(recipient=human).count()

notify(
    recipient_ids=[human.id],
    notification_type="question_asked",
    title="[TEST] Should be suppressed by quiet hours",
    board=board,
)

after = Notification.objects.filter(recipient=human).count()
check("Notification suppressed (quiet hours)", after == before)

# Restore
pref.quiet_hours_start = old_start
pref.quiet_hours_end = old_end
pref.save()


# ── Test 6: All 6 notification types ────────────────────────────────────

header("TEST 6: All notification types create records")

types = [
    ("task_assigned", "assigned to you"),
    ("comment_added", "new comment"),
    ("status_changed", "status changed"),
    ("planning_complete", "planning done"),
    ("question_asked", "agent has a question"),
    ("spec_finished", "spec completed"),
]

for ntype, desc in types:
    before = Notification.objects.filter(recipient=human).count()
    notify(
        recipient_ids=[human.id],
        notification_type=ntype,
        title=f"[TEST] {desc}",
        board=board,
    )
    after = Notification.objects.filter(recipient=human).count()
    check(f"Type '{ntype}' created notification", after == before + 1)


# ── Test 7: Signal-based notifications (status change) ──────────────────

header("TEST 7: TaskHistory signal triggers status_changed notification")

# Clear board membership for human to re-add (ensure they get the signal notification)
BoardMembership.objects.get_or_create(board=task.board, user=human)

before = Notification.objects.filter(
    recipient=human, notification_type="status_changed"
).count()

old_status = task.status
new_status = TaskStatus.IN_PROGRESS if task.status != TaskStatus.IN_PROGRESS else TaskStatus.REVIEW

TaskHistory.objects.create(
    task=task,
    field_name="status",
    old_value=old_status,
    new_value=new_status,
    changed_by="test-runner@taskit.local",
)

# Give signals a moment to fire
time.sleep(0.5)

after = Notification.objects.filter(
    recipient=human, notification_type="status_changed"
).count()
check(
    "Signal created status_changed notification",
    after > before,
    f"before={before} after={after}",
)


# ── Test 8: API endpoints (via Django test client) ──────────────────────

header("TEST 8: API endpoints")

from django.test import RequestFactory
from tasks.notification_views import (
    NotificationViewSet,
    notification_preferences,
)

factory = RequestFactory()

# GET /api/notifications/unread_count/
req = factory.get("/api/notifications/unread_count/")
view = NotificationViewSet.as_view({"get": "unread_count"})
resp = view(req)
check(
    f"GET unread_count → {resp.data}",
    resp.status_code == 200 and "count" in resp.data,
)

# GET /api/notifications/preferences/
req = factory.get("/api/notifications/preferences/")
resp = notification_preferences(req)
check(
    "GET preferences → 200",
    resp.status_code == 200 and "sound_enabled" in resp.data,
)


# ── Summary ──────────────────────────────────────────────────────────────

header("SUMMARY")

total = _pass + _fail
print(f"\n  {GREEN}{_pass} passed{RESET}  {RED}{_fail} failed{RESET}  ({total} total)\n")

if _fail == 0:
    print(f"  {GREEN}{BOLD}All tests passed!{RESET}\n")
else:
    print(f"  {YELLOW}Some tests failed — check output above.{RESET}\n")

sys.exit(0 if _fail == 0 else 1)
