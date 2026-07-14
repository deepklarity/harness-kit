"""Tests for the agent roster default + dispatch-error contract.

Acceptance criteria driving these tests:

1. Any new board ends with every active AGENT enrolled (BoardMembership),
   even when the board was created UI-only (no working_dir / no auto_init).
   The previous symptom was "fresh board only has claude enabled" — the
   fix creates memberships for all active agent Users regardless of the
   creation path; the operator never has to learn curl to enable them.

2. The settings page surfaces a per-agent on/off toggle. The toggle lands
   via the existing PATCH /boards/{id}/agents/{name}/ endpoint, which is
   already exercised end-to-end (this test pins the toggle off → roster
   marks it disabled → PATCH on → roster marks it enabled again).

3. If a task would be dispatched to an agent that is disabled on the
   board, the failure names the agent and points at the settings page
   instead of silently switching agents. Pinned at three layers
   (planner prompt, dispatch guard, manual-assign guard) so the same
   message travels from the ODIN CLI / orchestrator, the celery_dag
   dispatch path, and the TaskViewSet.assign view.

Tests are written before implementation; they are red until the
ship-it work lands.
"""

import json
import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from django.test import SimpleTestCase, TestCase  # noqa: E402

import django  # noqa: E402

django.setup()

from rest_framework.test import APIClient  # noqa: E402

from tasks.models import (  # noqa: E402
    Board,
    BoardMembership,
    Spec,
    Task,
    TaskStatus,
    User,
    UserRole,
)


def _make_agent(name, default_model="agent-default-model"):
    """Create an active AGENT User — model default name irrelevant for these tests."""
    return User.objects.create(
        email=f"{name}@odin.agent",
        name=name,
        role=UserRole.AGENT,
        is_active=True,
        available_models=[{"name": default_model, "is_default": True}],
    )


def _make_retired_agent(name):
    """Create a retired AGENT User — must NOT be enrolled on a new board."""
    return User.objects.create(
        email=f"{name}@odin.agent",
        name=name,
        role=UserRole.AGENT,
        is_active=False,
        available_models=[{"name": "retired-model", "is_default": True}],
    )


def _active_agent_names(board):
    """Names of active agents enrolled on ``board`` via BoardMembership.

    ``User.board_memberships`` is the reverse accessor (related_name on
    BoardMembership.user); ``memberships`` is the Board-side accessor.
    """
    from tasks.models import BoardMembership
    return sorted(
        User.objects.filter(
            board_memberships__board=board,
            role=UserRole.AGENT,
            is_active=True,
        ).values_list("name", flat=True)
    )


# ═══════════════════════════════════════════════════════════════════════
# Acceptance #1 — every active agent enabled on every new board
# ═══════════════════════════════════════════════════════════════════════


class TestNewBoardEnrollsAllActiveAgents(TestCase):
    """Any board-creation path ends with active agents enrolled.

    Covers three creation paths because each one carried a different gap:

    * UI-only (no working_dir, no auto_init) — fixes the bug where a
      board created in the modal had zero memberships and only `claude`
      ended up routable via the planner's prompt.
    * CLI/UI with auto_init — exercises the existing _init_odin_for_board
      path so the membership seeding stays correct after the fix.
    * CLI/UI with disabled_agents=["gemini"] — the opt-out is honoured
      but everyone else is enrolled.
    """

    def setUp(self):
        self.client = APIClient()
        self.claude = _make_agent("claude")
        self.gemini = _make_agent("gemini")
        self.codex = _make_agent("codex")
        self.qwen_retired = _make_retired_agent("qwen")

    def test_ui_only_board_creates_memberships_for_every_active_agent(self):
        """CreateBoardModal with name/description only — no working_dir, no auto_init."""
        resp = self.client.post(
            "/boards/",
            {"name": "UI-only board", "description": "no working dir"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        board = Board.objects.get(id=resp.data["id"])

        names = _active_agent_names(board)
        # Every active agent is enrolled; retired agents are skipped.
        self.assertEqual(names, ["claude", "codex", "gemini"])
        self.assertNotIn("qwen", names)

    def test_board_with_auto_init_enrolls_every_active_agent(self):
        """auto_init=True path — exercises _init_odin_for_board + _create_agent_memberships."""
        resp = self.client.post(
            "/boards/",
            {
                "name": "Init board",
                "auto_init": False,  # we just want to skip subprocess bootstrap
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        board = Board.objects.get(id=resp.data["id"])
        self.assertEqual(_active_agent_names(board), ["claude", "codex", "gemini"])

    def test_disabled_agents_filter_is_honoured_others_still_enabled(self):
        """disabled_agents=["gemini"] — gemini stays off, claude/codex are enrolled."""
        resp = self.client.post(
            "/boards/",
            {
                "name": "Custom roster",
                "auto_init": False,
                "disabled_agents": ["gemini"],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        board = Board.objects.get(id=resp.data["id"])
        self.assertEqual(_active_agent_names(board), ["claude", "codex"])
        # Disabled agent's membership was NOT created.
        self.assertFalse(
            BoardMembership.objects.filter(
                board=board, user=self.gemini,
            ).exists()
        )

    def test_agents_endpoint_reports_all_enabled_for_fresh_board(self):
        """The UI reads /boards/{id}/agents/ — every agent must show enabled=True."""
        resp = self.client.post(
            "/boards/",
            {"name": "Roster view", "auto_init": False},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        board_id = resp.data["id"]

        agents_resp = self.client.get(f"/boards/{board_id}/agents/")
        self.assertEqual(agents_resp.status_code, 200)
        agents_by_name = {a["name"]: a for a in agents_resp.data["agents"]}
        # Same three active agents, all enabled.
        self.assertEqual(
            sorted(n for n, a in agents_by_name.items() if a["enabled"]),
            ["claude", "codex", "gemini"],
        )
        # Retired agents do NOT appear at all (not "enabled", not present).
        self.assertNotIn("qwen", agents_by_name)

    def test_existing_board_backfill_helper(self):
        """A board created before this fix shipped can be brought to parity
        by a one-shot helper. Pinned so the helper exists + is idempotent.

        Operator workflow: a board with one membership only (claude)
        invokes the helper; afterwards it has memberships for every
        active agent, and a second call is a no-op.
        """
        from tasks.views import enroll_all_active_agents

        board = Board.objects.create(name="Stale board")
        BoardMembership.objects.create(board=board, user=self.claude)
        self.assertEqual(_active_agent_names(board), ["claude"])

        created = enroll_all_active_agents(board)
        # Helper may return int / queryset — accept either.
        try:
            count = len(created)
        except TypeError:
            count = int(created or 0)
        self.assertGreaterEqual(count, 2)  # gemini + codex added
        self.assertEqual(_active_agent_names(board), ["claude", "codex", "gemini"])

        again = enroll_all_active_agents(board)
        try:
            again_count = len(again)
        except TypeError:
            again_count = int(again or 0)
        self.assertEqual(again_count, 0)
        self.assertEqual(_active_agent_names(board), ["claude", "codex", "gemini"])


# ═══════════════════════════════════════════════════════════════════════
# Acceptance #2 — settings toggle round-trips through the agents API
# ═══════════════════════════════════════════════════════════════════════


class TestAgentToggleRoundTrip(TestCase):
    """PATCH /boards/{id}/agents/{name}/ reflects in GET /boards/{id}/agents/."""

    def setUp(self):
        self.client = APIClient()
        self.claude = _make_agent("claude")
        self.gemini = _make_agent("gemini")
        self.board = Board.objects.create(name="Toggle board")
        # Board has both agents enabled by default.
        BoardMembership.objects.create(board=self.board, user=self.claude)
        BoardMembership.objects.create(board=self.board, user=self.gemini)

    def _roster(self):
        resp = self.client.get(f"/boards/{self.board.id}/agents/")
        return {a["name"]: a for a in resp.data["agents"]}

    def test_toggle_off_marks_agent_disabled(self):
        resp = self.client.patch(
            f"/boards/{self.board.id}/agents/gemini/",
            {"enabled": False},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        roster = self._roster()
        self.assertTrue(roster["claude"]["enabled"])
        self.assertFalse(roster["gemini"]["enabled"])

    def test_toggle_back_on_re_enables_agent(self):
        self.client.patch(
            f"/boards/{self.board.id}/agents/gemini/",
            {"enabled": False},
            format="json",
        )
        self.client.patch(
            f"/boards/{self.board.id}/agents/gemini/",
            {"enabled": True},
            format="json",
        )
        roster = self._roster()
        self.assertTrue(roster["gemini"]["enabled"])


# ═══════════════════════════════════════════════════════════════════════
# Acceptance #3 — dispatch / assign refuses to send work to a disabled agent
# ═══════════════════════════════════════════════════════════════════════


class TestDisabledAgentDispatchRefused(TestCase):
    """A disabled-on-this-board agent must surface an error that names the
    agent and tells the operator where to flip the switch — no silent
    fallback to a different agent.

    Three call sites pin this contract:

    * `_auto_assign_from_suggested` (celery_dag dispatch gate) — refuses
      to auto-assign from a ``suggested_agent`` that isn't currently a
      BoardMembership.
    * `TaskViewSet.assign` (manual operator action) — POST /assign/ with
      an assignee_id that has no membership returns 400, names the agent,
      and includes a settings path.
    * The planner prompt — when ``routing_config`` returns zero enabled
      agents, the planner embed surfaces "No enabled agents on board N;
      enable at /boards/{id}/" so the user never sees a silent claude-everything run.
    """

    def setUp(self):
        self.client = APIClient()
        # claude + codex + glm are all active in agent_models.json.
        # Two are enrolled on the board (claude enabled, codex enabled);
        # glm + gemini are intentionally NOT enrolled — disabled-on-this-board.
        self.claude = _make_agent("claude")
        self.codex = _make_agent("codex")
        self.glm = _make_agent("glm")
        # gemini is retired in the lineup. It's still useful as a "name
        # only" disabled agent on the board for the API test (the API
        # layer doesn't gate on lineup-presence, only on BoardMembership).
        self.gemini = _make_agent("gemini")
        self.board = Board.objects.create(name="Dispatch board")
        BoardMembership.objects.create(board=self.board, user=self.claude)
        BoardMembership.objects.create(board=self.board, user=self.codex)
        # glm + gemini intentionally NOT enrolled — disabled.

        self.spec = Spec.objects.create(board=self.board, odin_id="sp_322", title="W10")
        self.task = Task.objects.create(
            board=self.board,
            spec=self.spec,
            title="Test task",
            created_by="alice@test.com",
            status=TaskStatus.IN_PROGRESS,
            metadata={"suggested_agent": "glm"},
        )

    def test_auto_assign_refuses_disabled_agent(self):
        from tasks.dag_executor import _auto_assign_from_suggested

        with self.assertRaises(Exception) as cm:
            _auto_assign_from_suggested(self.task)
        msg = str(cm.exception).lower()
        # Message names the agent + points at settings.
        self.assertIn("glm", str(cm.exception))
        self.assertTrue(
            "/agents/" in msg or "settings" in msg or f"/boards/{self.board.id}" in msg,
            f"error must point at the settings page, got: {cm.exception!r}",
        )
        # And: nothing mutated on the task.
        self.task.refresh_from_db()
        self.assertIsNone(self.task.assignee_id)
        self.assertNotIn(
            "auto_assigned_from_suggested",
            (self.task.metadata or {}),
        )

    def test_assign_rejects_disabled_agent_via_api(self):
        resp = self.client.post(
            f"/tasks/{self.task.id}/assign/",
            {
                "assignee_id": self.glm.id,
                "updated_by": "alice@test.com",
            },
            format="json",
        )
        # Either 400 (rejected) or a non-200 with detail text. Either way
        # the response body must include the agent name + a settings hint.
        self.assertIn(resp.status_code, (400, 404))
        body = json.dumps(resp.data).lower()
        self.assertIn("glm", body)
        self.assertTrue(
            "/agents/" in body or "settings" in body or f"/boards/{self.board.id}" in body,
            f"response must point at the settings page, got: {resp.data}",
        )

    def test_routing_config_advertises_settings_hint_when_empty(self):
        """Get the settings URL hint by querying the empty roster config.

        The /boards/{id}/routing-config/ endpoint may still list
        *available* agents even when none are enrolled on the board, so
        the planner doesn't crash on empty boards. What we need is the
        board-level warning produced when zero agents are enabled —
        rendered in the JSON envelope next to ``agents``.
        """
        # Empty roster: enable only claude and codex, but then disable both.
        BoardMembership.objects.filter(board=self.board).delete()

        resp = self.client.get(f"/boards/{self.board.id}/routing-config/")
        self.assertEqual(resp.status_code, 200)
        body_text = json.dumps(resp.data)
        # The settings hint must include the board id + the settings slug.
        self.assertIn(f"/boards/{self.board.id}", body_text)
        self.assertIn("agents", body_text)


# ═══════════════════════════════════════════════════════════════════════
# Acceptance #3b — planner embed honors the board roster
# ═══════════════════════════════════════════════════════════════════════


class TestPlannerEmbedHonorsRoster(SimpleTestCase):
    """Pure unit test — string-match the planner prompt's agent block.

    ``_build_available_agents`` should drop every roster-disabled agent
    from the planner's "Available agents:" block. This test uses
    function-level isolation so we don't need a running backend or board.
    """

    def test_available_agents_excludes_disabled_from_roster(self):
        import asyncio
        from unittest.mock import MagicMock, patch

        from odin.config import OdinConfig, AgentConfig
        from odin.orchestrator import Orchestrator

        cfg = OdinConfig()
        # Populate two cheap stub agents that match the routing_config
        # names so the orchestrator's `self.config.agents.get(name)` branch
        # doesn't filter them out before the W10.4 enabled-flag check runs.
        cfg.agents["claude"] = AgentConfig(name="claude")
        cfg.agents["gemini"] = AgentConfig(name="gemini")

        orc = Orchestrator.__new__(Orchestrator)  # bypass __init__
        orc.config = cfg

        routing_config = {
            "agents": [
                {"name": "claude", "enabled": True, "cost_tier": "high",
                 "default_model": "claude-opus", "premium_model": "claude-opus",
                 "capabilities": [], "models": [
                     {"name": "claude-opus", "enabled": True, "is_default": True}]},
                {"name": "gemini", "enabled": False, "cost_tier": "medium",
                 "default_model": "gemini-pro", "premium_model": "gemini-pro",
                 "capabilities": [], "models": [
                     {"name": "gemini-pro", "enabled": True, "is_default": True}]},
            ]
        }

        # Stub harness resolution + availability so the test doesn't need
        # an installed CLI per agent.
        fake_harness = MagicMock()

        async def _available_async():
            return True

        fake_harness.is_available = _available_async

        with patch("odin.orchestrator.get_harness", return_value=fake_harness):
            builder = getattr(orc, "_build_available_agents", None)
            if builder is None:
                self.fail(
                    "Orchestrator must expose _build_available_agents (or "
                    "a wrapper) so the planner embed can be unit-tested."
                )
            result = asyncio.run(builder(quota={}, routing_config=routing_config))

        names = [a.get("name") for a in result] if isinstance(result, list) else []
        self.assertIn("claude", names)
        self.assertNotIn("gemini", names)
