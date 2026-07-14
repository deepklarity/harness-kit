"""Tests for provider-agnostic reflection reviewer defaults.

The manual /reflect/ endpoint must not hardcode claude as the only valid
reviewer. A codex-only or glm-only board should be able to trigger
reflection without the default crashing against a non-existent claude CLI.

Covers:
- REFLECTION_ALLOWED_AGENTS includes all fable providers (not just claude/gemini/codex)
- Manual reflect without specifying reviewer derives from available agents
- Validation accepts glm/minimax as reviewer agents
"""

from unittest.mock import patch

from .base import APITestCase
from tasks.models import TaskStatus, User, UserRole
from tasks.serializers import REFLECTION_ALLOWED_AGENTS


class TestReflectionAllowedAgents(APITestCase):
    """REFLECTION_ALLOWED_AGENTS must not gate out valid providers."""

    def test_includes_glm(self):
        self.assertIn("glm", REFLECTION_ALLOWED_AGENTS)

    def test_includes_minimax(self):
        self.assertIn("minimax", REFLECTION_ALLOWED_AGENTS)

    def test_includes_codex(self):
        self.assertIn("codex", REFLECTION_ALLOWED_AGENTS)

    def test_includes_claude(self):
        self.assertIn("claude", REFLECTION_ALLOWED_AGENTS)


class TestReflectAcceptsAllProviders(APITestCase):
    """POST /tasks/:id/reflect/ accepts any fable provider as reviewer."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def _reflect(self, task_id, data=None):
        return self.client.post(f"/tasks/{task_id}/reflect/", data or {}, format="json")

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_reflect_accepts_glm_reviewer(self, mock_delay):
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        resp = self._reflect(task.id, {
            "reviewer_agent": "glm",
            "reviewer_model": "zai-coding-plan/glm-5.2",
        })
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.data["reviewer_agent"], "glm")

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_reflect_accepts_minimax_reviewer(self, mock_delay):
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        resp = self._reflect(task.id, {
            "reviewer_agent": "minimax",
            "reviewer_model": "minimax-coding-plan/MiniMax-M3",
        })
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.data["reviewer_agent"], "minimax")

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_reflect_rejects_unknown_agent(self, mock_delay):
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        resp = self._reflect(task.id, {
            "reviewer_agent": "nonexistent",
            "reviewer_model": "x",
        })
        self.assertEqual(resp.status_code, 400)


class TestReflectDerivesFromAvailable(APITestCase):
    """When the caller doesn't specify a reviewer, the default derives from
    available agents — not a hardcoded claude that may not be installed."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def _reflect(self, task_id, data=None):
        return self.client.post(f"/tasks/{task_id}/reflect/", data or {}, format="json")

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_default_derives_to_codex_when_only_codex_available(self, mock_delay):
        """Codex-only board: manual reflect without specifying reviewer
        should derive codex from available agents, not crash on claude."""
        User.objects.create(
            name="codex", email="codex@odin.agent", role=UserRole.AGENT,
            is_active=True,
            available_models=[{"name": "gpt-5.5", "is_default": True}],
        )
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        resp = self._reflect(task.id)
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.data["reviewer_agent"], "codex")
        self.assertEqual(resp.data["reviewer_model"], "gpt-5.5")

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_falls_back_to_serializer_default_when_no_agents(self, mock_delay):
        """No agent users in DB → backward-compatible fallback to the
        serializer default (claude), preserving existing behavior."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        resp = self._reflect(task.id)
        self.assertEqual(resp.status_code, 202)
