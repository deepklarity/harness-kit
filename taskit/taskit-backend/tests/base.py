"""Shared test helpers and base class for taskit API tests."""

import os

# Force SQLite for tests — no PostgreSQL dependency
os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from tasks.models import Board, BoardMembership, Label, Spec, Task, TaskHistory, User


@override_settings(FIREBASE_AUTH_ENABLED=False)
class APITestCase(TestCase):
    """Base class providing APIClient and common factory helpers."""

    def setUp(self):
        self.client = APIClient()

    def results(self, resp):
        """Extract results from a paginated DRF response."""
        return resp.data["results"]

    # ── Factory helpers ──────────────────────────────────────────────

    def make_user(self, name="Alice", email="alice@test.com", **kwargs):
        return User.objects.create(name=name, email=email, **kwargs)

    def make_board(self, name="Sprint 1", **kwargs):
        return Board.objects.create(name=name, **kwargs)

    def make_label(self, name="bug", color="#ef4444", **kwargs):
        return Label.objects.create(name=name, color=color, **kwargs)

    def make_spec(self, board, odin_id="sp_001", title="Test Spec", **kwargs):
        return Spec.objects.create(board=board, odin_id=odin_id, title=title, **kwargs)

    def make_task(self, board, title="Fix login bug", created_by="alice@test.com", **kwargs):
        return Task.objects.create(
            board=board, title=title, created_by=created_by, **kwargs
        )

    def make_task_via_api(self, board, title="Fix login bug", created_by="alice@test.com", **kwargs):
        """Create a task through the API (triggers history tracking)."""
        data = {"board_id": board.id, "title": title, "created_by": created_by, **kwargs}
        return self.client.post("/tasks/", data, format="json")
