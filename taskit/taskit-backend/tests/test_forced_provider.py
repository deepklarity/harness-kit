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

    @override_settings(FORCED_BASE_PROVIDER="gemini", FORCED_BASE_MODEL=None)
    @patch("tasks.forced_provider.shutil.which", return_value="/usr/bin/gemini")
    def test_selection_uses_provider_default_model(self, _mock_which):
        selection = get_forced_provider_selection()
        self.assertTrue(selection.enabled)
        self.assertEqual(selection.provider, "gemini")
        self.assertEqual(selection.model, "gemini-3-flash-preview")

    @override_settings(FORCED_BASE_PROVIDER="qwen", FORCED_BASE_MODEL="coder-model")
    @patch("tasks.forced_provider.shutil.which", return_value="/usr/bin/qwen")
    def test_selection_uses_pinned_model(self, _mock_which):
        selection = get_forced_provider_selection()
        self.assertTrue(selection.enabled)
        self.assertEqual(selection.provider, "qwen")
        self.assertEqual(selection.model, "coder-model")

    @override_settings(FORCED_BASE_PROVIDER="claude", FORCED_BASE_MODEL=None)
    def test_invalid_provider_raises(self):
        with self.assertRaises(ImproperlyConfigured):
            get_forced_provider_selection()


class TestForcedProviderEndpoints(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    @override_settings(FORCED_BASE_PROVIDER="gemini", FORCED_BASE_MODEL="gemini-2.5-pro")
    @patch("tasks.forced_provider.shutil.which", return_value="/usr/bin/gemini")
    def test_runtime_forced_provider_endpoint(self, _mock_which):
        resp = self.client.get("/api/runtime/forced-provider/")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["enabled"])
        self.assertEqual(resp.data["provider"], "gemini")
        self.assertEqual(resp.data["model"], "gemini-2.5-pro")

    @override_settings(FORCED_BASE_PROVIDER="gemini", FORCED_BASE_MODEL="gemini-2.5-pro")
    @patch("tasks.forced_provider.shutil.which", return_value="/usr/bin/gemini")
    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_manual_reflection_request_is_forced(self, mock_delay, _mock_which):
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        resp = self.client.post(
            f"/tasks/{task.id}/reflect/",
            {
                "reviewer_agent": "claude",
                "reviewer_model": "claude-opus-4-6",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 202)
        report = ReflectionReport.objects.get(task=task)
        self.assertEqual(report.reviewer_agent, "gemini")
        self.assertEqual(report.reviewer_model, "gemini-2.5-pro")
        mock_delay.assert_called_once_with(report.id)

    @override_settings(FORCED_BASE_PROVIDER="qwen", FORCED_BASE_MODEL="coder-model")
    @patch("tasks.forced_provider.shutil.which", return_value="/usr/bin/qwen")
    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_auto_reflection_uses_forced_provider(self, mock_delay, _mock_which):
        task = self.make_task(self.board, status=TaskStatus.IN_PROGRESS)
        resp = self.client.put(
            f"/tasks/{task.id}/",
            {"status": "REVIEW", "updated_by": "admin@test.com"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        report = ReflectionReport.objects.get(task=task)
        self.assertEqual(report.reviewer_agent, "qwen")
        self.assertEqual(report.reviewer_model, "coder-model")
        mock_delay.assert_called_once_with(report.id)
