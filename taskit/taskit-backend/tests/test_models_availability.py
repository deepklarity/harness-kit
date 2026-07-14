"""Tests for model availability feature.

Covers: seedmodels command, model_name on tasks, available_models on users,
auto-add model to user, and history tracking for model_name changes.
"""

import json
import tempfile
from io import StringIO

from django.core.management import call_command

from .base import APITestCase
from tasks.models import User


# ═══════════════════════════════════════════════════════════════════════
# Seed Models Command
# ═══════════════════════════════════════════════════════════════════════


class TestSeedModelsCommand(APITestCase):

    def setUp(self):
        super().setUp()
        # Lineup fixture files must live in a dir that exists on any host,
        # not a hardcoded /tmp path from the authoring sandbox.
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_seedmodels_creates_agent_users(self):
        from tasks.pricing import get_active_agents
        out = StringIO()
        call_command("seedmodels", stdout=out)
        output = out.getvalue()

        # Drift test (per task #2): the lineup count is derived from
        # agent_models.json via pricing.get_active_agents (single source).
        # The active lineup changes over time (agy was just added) — tests
        # must survive that, so we never hardcode a count or list here.
        expected = get_active_agents()
        self.assertEqual(
            User.objects.filter(email__endswith="@odin.agent").count(),
            len(expected),
        )
        # Spot-check that the canonical claude user was created — but don't
        # pin the full agent list as a literal.
        self.assertIn("claude@odin.agent", output)
        for agent in expected:
            self.assertIn(f"{agent}@odin.agent", output)

    def test_seedmodels_populates_models(self):
        from tasks.pricing import _agent_registry
        call_command("seedmodels", stdout=StringIO())

        claude = User.objects.get(email="claude@odin.agent")
        self.assertGreater(len(claude.available_models), 0)
        # Drift test (per task #2): derive expected claude model names from
        # agent_models.json instead of pinning literals that drift when
        # Sonnet/Opus versions roll.
        registry_models = {
            m["name"] for m in _agent_registry()["claude"]["models"]
            if not m.get("retired")
        }
        model_names = [m["name"] for m in claude.available_models]
        for name in registry_models:
            self.assertIn(name, model_names)

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

    def test_seedmodels_marks_active_agent_users_active(self):
        """Default seedmodels sets is_active=True for every agent in agent_models.json.

        Drift test (per task #135): we verify is_active for the active lineup
        rather than pinning individual agent names, so this test survives as
        agents are added/removed over time.
        """
        from tasks.pricing import get_active_agents
        call_command("seedmodels", stdout=StringIO())

        for agent_name in get_active_agents():
            user = User.objects.get(email=f"{agent_name}@odin.agent")
            self.assertTrue(
                user.is_active,
                f"Active lineup agent '{agent_name}' should have is_active=True after seedmodels",
            )

    def test_seedmodels_deactivates_retired_agent_user(self):
        """Re-seeding after an agent is removed from agent_models.json deactivates that User.

        Reproduces the bug from task #135: seedmodels merges but never prunes,
        so retired agents (qwen, gemini at the time of writing) remain in the
        DB with full models and leak into routing/UI queries. After re-seed,
        the retired agent User should have is_active=False but still exist
        (FK integrity for task history).
        """
        # Step 1: seed a lineup including qwen as a "legacy" provider.
        legacy = {"agents": {
            "claude": {
                "color": "#8b5cf6",
                "cost_tier": "high",
                "cli_command": "claude",
                "default_model": "claude-sonnet-5",
                "models": [
                    {"name": "claude-sonnet-5", "is_default": True},
                ],
            },
            "qwen": {
                "color": "#777777",
                "cost_tier": "low",
                "cli_command": "qwen",
                "default_model": "qwen3",
                "models": [
                    {"name": "qwen3", "is_default": True},
                ],
            },
        }}
        legacy_path = f"{self._tmp.name}/seedmodels_legacy.json"
        with open(legacy_path, "w") as f:
            json.dump(legacy, f)
        call_command("seedmodels", "--file", legacy_path, stdout=StringIO())

        qwen = User.objects.get(email="qwen@odin.agent")
        claude = User.objects.get(email="claude@odin.agent")
        self.assertTrue(qwen.is_active)
        self.assertTrue(claude.is_active)

        # Step 2: re-seed with qwen removed (the upstream scenario).
        current_path = (
            __import__("pathlib").Path(__file__).resolve()
            .parent.parent / "data" / "agent_models.json"
        )
        call_command("seedmodels", "--file", str(current_path), stdout=StringIO())

        # Step 3: qwen should now be deactivated but the User record must remain
        # so historical tasks/comments still resolve the FK.
        qwen.refresh_from_db()
        claude.refresh_from_db()
        self.assertFalse(
            qwen.is_active,
            "qwen (no longer in agent_models.json) should be deactivated after re-seed",
        )
        self.assertTrue(
            claude.is_active,
            "claude (still in agent_models.json) should remain active",
        )
        self.assertTrue(
            User.objects.filter(email="qwen@odin.agent").exists(),
            "Retired agent User record must be preserved for FK integrity",
        )

    def test_seedmodels_removes_retired_models_from_active_user(self):
        """Models absent from agent_models.json are dropped from active agent users.

        Even when the agent is still active, individual models the JSON no
        longer lists must be pruned from that user's available_models. Without
        this, the DB lineup diverges from the JSON single source of truth.
        """
        legacy = {"agents": {
            "claude": {
                "color": "#8b5cf6",
                "cost_tier": "high",
                "cli_command": "claude",
                "default_model": "claude-sonnet-5",
                "models": [
                    {"name": "claude-sonnet-5", "is_default": True},
                    {"name": "claude-old-deprecated", "is_default": False},
                ],
            },
        }}
        legacy_path = f"{self._tmp.name}/seedmodels_claude_legacy.json"
        with open(legacy_path, "w") as f:
            json.dump(legacy, f)
        call_command("seedmodels", "--file", legacy_path, stdout=StringIO())

        claude = User.objects.get(email="claude@odin.agent")
        names = {m["name"] for m in claude.available_models}
        self.assertIn("claude-old-deprecated", names)

        # Re-seed with current JSON that does NOT list claude-old-deprecated.
        current_path = (
            __import__("pathlib").Path(__file__).resolve()
            .parent.parent / "data" / "agent_models.json"
        )
        call_command("seedmodels", "--file", str(current_path), stdout=StringIO())

        claude.refresh_from_db()
        names = {m["name"] for m in claude.available_models}
        self.assertNotIn(
            "claude-old-deprecated", names,
            "Retired model should be pruned from active agent's available_models",
        )
        # claude-sonnet-5 still in the current JSON, so it must remain.
        self.assertIn("claude-sonnet-5", names)

    def test_seedmodels_reactivates_returning_agent(self):
        """An agent added back to agent_models.json after being retired flips is_active back to True."""
        legacy = {"agents": {
            "claude": {
                "color": "#8b5cf6",
                "cost_tier": "high",
                "cli_command": "claude",
                "default_model": "claude-sonnet-5",
                "models": [{"name": "claude-sonnet-5", "is_default": True}],
            },
        }}
        legacy_path = f"{self._tmp.name}/seedmodels_claude_only.json"
        with open(legacy_path, "w") as f:
            json.dump(legacy, f)
        call_command("seedmodels", "--file", legacy_path, stdout=StringIO())

        # Seed a lineup that includes glm, then re-seed without it.
        with_glm = {"agents": {
            **legacy["agents"],
            "glm": {
                "color": "#eab308",
                "cost_tier": "low",
                "cli_command": "opencode",
                "default_model": "glm-5",
                "models": [{"name": "glm-5", "is_default": True}],
            },
        }}
        with_glm_path = f"{self._tmp.name}/seedmodels_with_glm.json"
        with open(with_glm_path, "w") as f:
            json.dump(with_glm, f)
        call_command("seedmodels", "--file", with_glm_path, stdout=StringIO())
        glm = User.objects.get(email="glm@odin.agent")
        self.assertTrue(glm.is_active)

        call_command("seedmodels", "--file", legacy_path, stdout=StringIO())
        glm.refresh_from_db()
        self.assertFalse(glm.is_active, "Re-seeding without glm should deactivate glm")

        # Now re-add glm to the lineup — must reactivate.
        call_command("seedmodels", "--file", with_glm_path, stdout=StringIO())
        glm.refresh_from_db()
        self.assertTrue(glm.is_active, "Re-seeding with glm again should reactivate glm")

    def test_seedmodels_no_prune_preserves_user_added_models(self):
        """`--no-prune` keeps the legacy merge behavior: models not in JSON are preserved.

        Backward-compat for users who add models manually via API and don't
        want a seedmodels run to wipe them. Default is prune; opt out with --no-prune.
        """
        custom = {"agents": {
            "claude": {
                "color": "#8b5cf6",
                "cost_tier": "high",
                "cli_command": "claude",
                "default_model": "claude-sonnet-5",
                "models": [{"name": "claude-sonnet-5", "is_default": True}],
            },
        }}
        custom_path = f"{self._tmp.name}/seedmodels_no_prune.json"
        with open(custom_path, "w") as f:
            json.dump(custom, f)
        call_command("seedmodels", "--file", custom_path, stdout=StringIO())

        claude = User.objects.get(email="claude@odin.agent")
        # Simulate a user-added model not in JSON.
        claude.available_models = list(claude.available_models) + [
            {"name": "user-custom-model", "is_default": False},
        ]
        claude.save()

        call_command("seedmodels", "--file", custom_path, "--no-prune", stdout=StringIO())
        claude.refresh_from_db()
        names = {m["name"] for m in claude.available_models}
        self.assertIn(
            "user-custom-model", names,
            "--no-prune must preserve user-added models not in JSON",
        )


class TestActiveLineupQueries(APITestCase):
    """Active-lineup query paths must reflect the pruned set after seedmodels.

    Covers the second acceptance criterion: pricing.get_active_agents and any
    DB query that powers routing/UI must exclude retired agent Users.
    """

    def test_pricing_active_agents_excludes_retired(self):
        """get_active_agents() (single source: agent_models.json) excludes retired names."""
        from tasks.pricing import get_active_agents
        active = get_active_agents()
        self.assertNotIn("qwen", active)
        self.assertNotIn("gemini", active)

    def test_db_active_agent_query_excludes_retired(self):
        """User.objects.filter(role=AGENT, is_active=True) excludes retired users.

        This is the DB-side guard the UI/routing uses when materializing the
        agent list (members, board agents endpoint, dispatch guardrails).
        """
        call_command("seedmodels", stdout=StringIO())
        # Manually create a "retired" agent User to simulate drift.
        User.objects.create(
            name="qwen", email="qwen@odin.agent",
            role="AGENT", is_active=False,
            available_models=[{"name": "qwen3"}],
        )

        active_qs = User.objects.filter(role="AGENT", is_active=True)
        emails = {u.email for u in active_qs}
        self.assertIn("claude@odin.agent", emails)
        self.assertNotIn("qwen@odin.agent", emails)

    def test_task_history_loads_for_retired_agent_user(self):
        """Retired agent User record is preserved so historical tasks/comments still load.

        Even after deactivation, the User row must remain so FK references
        from TaskComment / TaskHistory still resolve. (We check via User
        existence and a basic property — comments are loaded as emails, not
        FK to User, so they would render regardless, but the User itself
        must still exist for any code that looks it up by id.)
        """
        call_command("seedmodels", stdout=StringIO())

        # Simulate drift: a retired user with a comment on a task.
        qwen = User.objects.create(
            name="qwen", email="qwen@odin.agent",
            role="AGENT", is_active=False,
        )
        board = self.make_board()
        task = self.make_task(board, created_by="qwen@odin.agent")

        # Comment authored by the retired user (email is the link).
        from tasks.models import TaskComment
        comment = TaskComment.objects.create(
            task=task,
            author_email=qwen.email,
            author_label="qwen",
            content="retired agent comment",
        )

        # After "re-seed" that deactivates qwen (manually here, but the same
        # state the new seedmodels default produces):
        self.assertFalse(qwen.is_active)
        self.assertTrue(User.objects.filter(email="qwen@odin.agent").exists())

        # History must still load — both TaskHistory rows and the comment.
        from tasks.models import TaskHistory
        TaskHistory.objects.create(
            task=task, field_name="created",
            old_value="", new_value=str(task.id), changed_by=qwen.email,
        )
        resp = self.client.get(f"/tasks/{task.id}/history/")
        self.assertEqual(resp.status_code, 200)

        comments_resp = self.client.get(f"/tasks/{task.id}/comments/")
        self.assertEqual(comments_resp.status_code, 200)
        self.assertEqual(len(comments_resp.data["results"]), 1)
        self.assertEqual(comments_resp.data["results"][0]["author_email"], "qwen@odin.agent")


# ═══════════════════════════════════════════════════════════════════════
# Task model_name
# ═══════════════════════════════════════════════════════════════════════


class TestTaskModelName(APITestCase):

    def test_create_task_with_model_name(self):
        board = self.make_board()
        resp = self.make_task_via_api(
            board, title="Task with model",
            model_name="claude-sonnet-5",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["model_name"], "claude-sonnet-5")

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
            model_name="claude-sonnet-5",
            assignee_id=user.id,
        )

        user.refresh_from_db()
        model_names = [m["name"] for m in user.available_models]
        self.assertIn("claude-sonnet-5", model_names)

    def test_create_task_uses_assignee_default_model_when_missing(self):
        from tasks.pricing import get_agent_default_model
        board = self.make_board()
        user = self.make_user(
            name="claude",
            email="claude@odin.agent",
            available_models=[
                {"name": "claude-opus-4-8", "description": "strong", "is_default": False},
                {"name": "claude-sonnet-5", "description": "fast", "is_default": True},
            ],
        )
        resp = self.make_task_via_api(
            board, title="Task default model",
            assignee_id=user.id,
        )
        self.assertEqual(resp.status_code, 201)
        # Default First (F45): the active lineup's default (agent_models.json)
        # is the single source of truth for AGENT users; the per-user
        # available_models.is_default is the fallback for unknown agents.
        self.assertEqual(resp.data["model_name"], get_agent_default_model("claude"))

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
        task = self.make_task(board, model_name="claude-sonnet-5")

        resp = self.client.put(
            f"/tasks/{task.id}/",
            {"model_name": "claude-opus-4-8", "updated_by": "admin@example.com"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["model_name"], "claude-opus-4-8")

        # Check history was created
        history_resp = self.client.get(f"/tasks/{task.id}/history/")
        history = self.results(history_resp)
        model_changes = [h for h in history if h["field_name"] == "model_name"]
        self.assertEqual(len(model_changes), 1)
        self.assertEqual(model_changes[0]["old_value"], "claude-sonnet-5")
        self.assertEqual(model_changes[0]["new_value"], "claude-opus-4-8")


# ═══════════════════════════════════════════════════════════════════════
# User available_models API
# ═══════════════════════════════════════════════════════════════════════


class TestUserAvailableModels(APITestCase):

    def test_available_models_in_user_response(self):
        models = [
            {"name": "claude-sonnet-5", "description": "fast", "is_default": True},
        ]
        user = self.make_user(available_models=models)
        resp = self.client.get(f"/users/{user.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["available_models"]), 1)
        self.assertEqual(resp.data["available_models"][0]["name"], "claude-sonnet-5")

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
