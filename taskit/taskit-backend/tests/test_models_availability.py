"""Tests for model availability feature.

Covers: seedmodels command, model_name on tasks, available_models on users,
auto-add model to user, and history tracking for model_name changes.
"""

import json
from io import StringIO

from django.core.management import call_command

from .base import APITestCase
from tasks.models import User


# ═══════════════════════════════════════════════════════════════════════
# Seed Models Command
# ═══════════════════════════════════════════════════════════════════════


class TestSeedModelsCommand(APITestCase):

    def test_seedmodels_creates_agent_users(self):
        out = StringIO()
        call_command("seedmodels", stdout=out)
        output = out.getvalue()

        # Should create 6 agent users
        self.assertEqual(User.objects.filter(email__endswith="@odin.agent").count(), 6)
        self.assertIn("claude@odin.agent", output)

    def test_seedmodels_populates_models(self):
        call_command("seedmodels", stdout=StringIO())

        claude = User.objects.get(email="claude@odin.agent")
        self.assertGreater(len(claude.available_models), 0)
        model_names = [m["name"] for m in claude.available_models]
        self.assertIn("claude-sonnet-4-5", model_names)
        self.assertIn("claude-opus-4", model_names)

    def test_seedmodels_sets_color(self):
        call_command("seedmodels", stdout=StringIO())
        claude = User.objects.get(email="claude@odin.agent")
        self.assertEqual(claude.color, "#8b5cf6")

    def test_seedmodels_idempotent(self):
        """Running seedmodels twice should not duplicate models or users."""
        call_command("seedmodels", stdout=StringIO())
        first_count = User.objects.filter(email__endswith="@odin.agent").count()
        claude_models_count = len(User.objects.get(email="claude@odin.agent").available_models)

        call_command("seedmodels", stdout=StringIO())
        second_count = User.objects.filter(email__endswith="@odin.agent").count()
        claude_models_count_2 = len(User.objects.get(email="claude@odin.agent").available_models)

        self.assertEqual(first_count, second_count)
        self.assertEqual(claude_models_count, claude_models_count_2)

    def test_seedmodels_dry_run(self):
        out = StringIO()
        call_command("seedmodels", "--dry-run", stdout=out)
        output = out.getvalue()

        self.assertIn("DRY RUN", output)
        self.assertEqual(User.objects.filter(email__endswith="@odin.agent").count(), 0)


# ═══════════════════════════════════════════════════════════════════════
# Task model_name
# ═══════════════════════════════════════════════════════════════════════


class TestTaskModelName(APITestCase):

    def test_create_task_with_model_name(self):
        board = self.make_board()
        resp = self.make_task_via_api(
            board, title="Task with model",
            model_name="claude-sonnet-4-5",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["model_name"], "claude-sonnet-4-5")

    def test_create_task_model_name_from_metadata(self):
        """When no explicit model_name, fallback to metadata.selected_model."""
        board = self.make_board()
        resp = self.make_task_via_api(
            board, title="Task with metadata model",
            metadata={"selected_model": "gemini-2.5-flash"},
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["model_name"], "gemini-2.5-flash")

    def test_create_task_auto_adds_model_to_assignee(self):
        board = self.make_board()
        user = self.make_user(name="claude", email="claude@odin.agent")
        self.assertEqual(len(user.available_models), 0)

        self.make_task_via_api(
            board, title="Task with model",
            model_name="claude-sonnet-4-5",
            assignee_id=user.id,
        )

        user.refresh_from_db()
        model_names = [m["name"] for m in user.available_models]
        self.assertIn("claude-sonnet-4-5", model_names)

    def test_create_task_uses_assignee_default_model_when_missing(self):
        board = self.make_board()
        user = self.make_user(
            name="claude",
            email="claude@odin.agent",
            available_models=[
                {"name": "claude-opus-4", "description": "strong", "is_default": False},
                {"name": "claude-sonnet-4-5", "description": "fast", "is_default": True},
            ],
        )
        resp = self.make_task_via_api(
            board, title="Task default model",
            assignee_id=user.id,
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["model_name"], "claude-sonnet-4-5")

    def test_create_task_explicit_model_overrides_assignee_default(self):
        board = self.make_board()
        user = self.make_user(
            name="gemini",
            email="gemini@odin.agent",
            available_models=[
                {"name": "gemini-2.5-pro", "description": "", "is_default": True},
            ],
        )
        resp = self.make_task_via_api(
            board, title="Task explicit model",
            assignee_id=user.id,
            model_name="gemini-2.5-flash",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["model_name"], "gemini-2.5-flash")

    def test_update_task_model_name_tracked_in_history(self):
        board = self.make_board()
        task = self.make_task(board, model_name="claude-sonnet-4-5")

        resp = self.client.put(
            f"/tasks/{task.id}/",
            {"model_name": "claude-opus-4", "updated_by": "admin@example.com"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["model_name"], "claude-opus-4")

        # Check history was created
        history_resp = self.client.get(f"/tasks/{task.id}/history/")
        history = self.results(history_resp)
        model_changes = [h for h in history if h["field_name"] == "model_name"]
        self.assertEqual(len(model_changes), 1)
        self.assertEqual(model_changes[0]["old_value"], "claude-sonnet-4-5")
        self.assertEqual(model_changes[0]["new_value"], "claude-opus-4")


# ═══════════════════════════════════════════════════════════════════════
# User available_models API
# ═══════════════════════════════════════════════════════════════════════


class TestUserAvailableModels(APITestCase):

    def test_available_models_in_user_response(self):
        models = [
            {"name": "claude-sonnet-4-5", "description": "fast", "is_default": True},
        ]
        user = self.make_user(available_models=models)
        resp = self.client.get(f"/users/{user.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["available_models"]), 1)
        self.assertEqual(resp.data["available_models"][0]["name"], "claude-sonnet-4-5")

    def test_update_user_available_models(self):
        user = self.make_user()
        new_models = [
            {"name": "model-a", "description": "first", "is_default": True},
            {"name": "model-b", "description": "second", "is_default": False},
        ]
        resp = self.client.put(
            f"/users/{user.id}/",
            {"available_models": new_models},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["available_models"]), 2)
        self.assertEqual(resp.data["available_models"][0]["name"], "model-a")

    def test_available_models_default_empty(self):
        user = self.make_user()
        resp = self.client.get(f"/users/{user.id}/")
        self.assertEqual(resp.data["available_models"], [])

    def test_available_models_in_members_list(self):
        board = self.make_board()
        models = [{"name": "test-model", "description": "test", "is_default": True}]
        user = self.make_user(available_models=models)
        # Add user to board
        from tasks.models import BoardMembership
        BoardMembership.objects.create(board=board, user=user)

        resp = self.client.get(f"/api/members/?board_id={board.id}")
        self.assertEqual(resp.status_code, 200)
        users = resp.data["results"]
        matched = [u for u in users if u["email"] == user.email]
        self.assertEqual(len(matched), 1)
        self.assertEqual(len(matched[0]["available_models"]), 1)
