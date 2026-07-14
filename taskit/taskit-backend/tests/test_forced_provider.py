from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from tasks.forced_provider import get_forced_provider_selection
from tasks.models import ReflectionReport, TaskStatus

from .base import APITestCase


class TestForcedProviderConfig(SimpleTestCase):
    @override_settings(FORCED_BASE_PROVIDER=None, FORCED_BASE_MODEL=None)
    def test_selection_disabled_without_env(self):
        selection = get_forced_provider_selection()
        self.assertFalse(selection.enabled)
        self.assertIsNone(selection.provider)
        self.assertIsNone(selection.model)

    @override_settings(FORCED_BASE_PROVIDER="glm", FORCED_BASE_MODEL=None)
    @patch("tasks.forced_provider.shutil.which", return_value="/usr/bin/opencode")
    def test_selection_uses_provider_default_model(self, _mock_which):
        selection = get_forced_provider_selection()
        self.assertTrue(selection.enabled)
        self.assertEqual(selection.provider, "glm")
        self.assertEqual(selection.model, "zai-coding-plan/glm-5.2")

    @override_settings(FORCED_BASE_PROVIDER="glm", FORCED_BASE_MODEL="zai-coding-plan/glm-5.2")
    @patch("tasks.forced_provider.shutil.which", return_value="/usr/bin/opencode")
    def test_selection_uses_pinned_model(self, _mock_which):
        selection = get_forced_provider_selection()
        self.assertTrue(selection.enabled)
        self.assertEqual(selection.provider, "glm")
        self.assertEqual(selection.model, "zai-coding-plan/glm-5.2")

    @override_settings(FORCED_BASE_PROVIDER="gemini", FORCED_BASE_MODEL=None)
    def test_invalid_provider_raises(self):
        """Retired/unknown providers (absent from agent_models.json) must raise."""
        with self.assertRaises(ImproperlyConfigured):
            get_forced_provider_selection()


class TestForcedProviderEndpoints(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    @override_settings(FORCED_BASE_PROVIDER="glm", FORCED_BASE_MODEL="zai-coding-plan/glm-5.2")
    @patch("tasks.forced_provider.shutil.which", return_value="/usr/bin/opencode")
    def test_runtime_forced_provider_endpoint(self, _mock_which):
        resp = self.client.get("/api/runtime/forced-provider/")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["enabled"])
        self.assertEqual(resp.data["provider"], "glm")
        self.assertEqual(resp.data["model"], "zai-coding-plan/glm-5.2")

    @override_settings(FORCED_BASE_PROVIDER="glm", FORCED_BASE_MODEL="zai-coding-plan/glm-5.2")
    @patch("tasks.forced_provider.shutil.which", return_value="/usr/bin/opencode")
    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_manual_reflect_honors_explicit_caller_reviewer_over_forced_provider(self, mock_delay, _mock_which):
        """The forced-provider env knob must never override an explicit
        caller-supplied reviewer_agent/model on the manual reflect endpoint."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        resp = self.client.post(
            f"/tasks/{task.id}/reflect/",
            {
                "reviewer_agent": "claude",
                "reviewer_model": "claude-opus-4-8",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 202)
        report = ReflectionReport.objects.get(task=task)
        self.assertEqual(report.reviewer_agent, "claude")
        self.assertEqual(report.reviewer_model, "claude-opus-4-8")
        self.assertEqual(report.selection_reason, "caller_override")
        mock_delay.assert_called_once_with(report.id)

    @override_settings(FORCED_BASE_PROVIDER="glm", FORCED_BASE_MODEL="zai-coding-plan/glm-5.2")
    @patch("tasks.forced_provider.shutil.which", return_value="/usr/bin/opencode")
    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_auto_reflection_ignores_forced_provider(self, mock_delay, _mock_which):
        """Auto-reflection selection must not consult the forced-provider
        env knob at all — reviewer_order/fallback decides regardless."""
        from tasks.models import User, UserRole
        User.objects.get_or_create(
            email="claude@odin.agent",
            defaults={
                "name": "Claude",
                "role": UserRole.AGENT,
                "available_models": [{"name": "claude-sonnet-5", "is_default": True}],
            },
        )
        task = self.make_task(self.board, status=TaskStatus.IN_PROGRESS)
        resp = self.client.put(
            f"/tasks/{task.id}/",
            {"status": "REVIEW", "updated_by": "admin@test.com"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        report = ReflectionReport.objects.get(task=task)
        self.assertNotEqual(report.reviewer_agent, "glm")
        self.assertNotEqual(report.selection_reason, "forced_provider")
        mock_delay.assert_called_once_with(report.id)
