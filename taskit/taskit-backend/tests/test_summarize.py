"""Tests for the /tasks/:id/summarize/ endpoint (dispatch-based architecture).

Covers:
- Returns 202 Accepted and sets metadata flag.
- Dispatches via execution strategy when configured.
- Falls back to direct subprocess when no strategy configured.
- Returns 404 for missing task.
"""

from unittest.mock import MagicMock, patch

from .base import APITestCase


class TestSummarizeEndpoint(APITestCase):
    """POST /tasks/:id/summarize/ tests."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board, title="Fix login bug")

    @patch("tasks.execution.get_strategy")
    def test_returns_202_accepted(self, mock_get_strategy):
        """202 returned with status=summarizing."""
        mock_get_strategy.return_value = MagicMock()
        resp = self.client.post(f"/tasks/{self.task.id}/summarize/", format="json")
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.data["status"], "summarizing")

    @patch("tasks.execution.get_strategy")
    def test_sets_metadata_flag(self, mock_get_strategy):
        """summarize_in_progress metadata flag is set on the task."""
        mock_get_strategy.return_value = MagicMock()
        self.client.post(f"/tasks/{self.task.id}/summarize/", format="json")

        self.task.refresh_from_db()
        self.assertTrue(self.task.metadata.get("summarize_in_progress"))

    @patch("tasks.execution.get_strategy")
    def test_dispatches_via_strategy(self, mock_get_strategy):
        """When a strategy is configured, trigger_summarize is called."""
        mock_strategy = MagicMock()
        mock_get_strategy.return_value = mock_strategy

        resp = self.client.post(f"/tasks/{self.task.id}/summarize/", format="json")

        self.assertEqual(resp.status_code, 202)
        mock_strategy.trigger_summarize.assert_called_once()
        call_task = mock_strategy.trigger_summarize.call_args[0][0]
        self.assertEqual(call_task.id, self.task.id)

    @patch("tasks.execution.base.spawn_summarize_subprocess")
    @patch("tasks.execution.get_strategy")
    def test_falls_back_to_subprocess(self, mock_get_strategy, mock_spawn):
        """When no strategy configured, spawns subprocess directly."""
        mock_get_strategy.return_value = None

        resp = self.client.post(f"/tasks/{self.task.id}/summarize/", format="json")

        self.assertEqual(resp.status_code, 202)
        mock_spawn.assert_called_once()

    def test_returns_404_for_missing_task(self):
        """404 returned for a task that does not exist."""
        resp = self.client.post("/tasks/99999/summarize/", format="json")
        self.assertEqual(resp.status_code, 404)
