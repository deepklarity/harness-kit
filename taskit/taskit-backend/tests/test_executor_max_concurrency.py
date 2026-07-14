"""Tests for executor max concurrency setting and capacity endpoint."""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from django.test import override_settings
from rest_framework.test import APIClient
from tests.base import APITestCase
from tasks.models import Task, TaskStatus, SystemSetting
from tasks.dag_executor import poll_and_execute


class SystemSettingTests(APITestCase):
    """Tests for SystemSetting model."""

    def test_create_max_concurrency_setting(self):
        """Test creating a max concurrency system setting."""
        setting = SystemSetting.objects.create(
            key="executor_max_concurrency",
            value=5,
        )
        self.assertEqual(setting.key, "executor_max_concurrency")
        self.assertEqual(setting.value, 5)

    def test_get_max_concurrency_setting(self):
        """Test retrieving max concurrency setting."""
        SystemSetting.objects.create(
            key="executor_max_concurrency",
            value=7,
        )
        setting = SystemSetting.objects.get(key="executor_max_concurrency")
        self.assertEqual(setting.value, 7)

    def test_update_max_concurrency_setting(self):
        """Test updating max concurrency setting."""
        setting = SystemSetting.objects.create(
            key="executor_max_concurrency",
            value=3,
        )
        setting.value = 5
        setting.save()
        self.assertEqual(setting.value, 5)

    def test_get_suggested_max_concurrency(self):
        """Test getting suggested max concurrency from RAM."""
        setting = SystemSetting.objects.create(
            key="executor_max_concurrency",
            value=3,
        )
        # Suggested max should be at least 1 (4GB per sandbox)
        suggested = setting.get_suggested_max()
        self.assertGreaterEqual(suggested, 1)


class CapacityEndpointTests(APITestCase):
    """Tests for the executor capacity endpoint."""

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.board = self.make_board()
        self.user = self.make_user()

    def test_capacity_endpoint_returns_running_and_max(self):
        """Test that capacity endpoint returns running count and max."""
        # Create a setting
        SystemSetting.objects.create(
            key="executor_max_concurrency",
            value=3,
        )

        response = self.client.get("/executor/capacity/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("running", data)
        self.assertIn("max", data)
        self.assertEqual(data["max"], 3)
        self.assertEqual(data["running"], 0)

    def test_capacity_endpoint_counts_executing_tasks(self):
        """Test that capacity endpoint counts EXECUTING tasks."""
        SystemSetting.objects.create(
            key="executor_max_concurrency",
            value=3,
        )

        # Create some executing tasks
        self.make_task(self.board, status=TaskStatus.EXECUTING)
        self.make_task(self.board, status=TaskStatus.EXECUTING)

        response = self.client.get("/executor/capacity/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["running"], 2)
        self.assertEqual(data["max"], 3)

    def test_capacity_endpoint_includes_suggestion(self):
        """Test that capacity endpoint includes suggested max."""
        SystemSetting.objects.create(
            key="executor_max_concurrency",
            value=2,
        )

        response = self.client.get("/executor/capacity/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("suggested_max", data)
        self.assertGreaterEqual(data["suggested_max"], 1)

    def test_capacity_endpoint_with_no_setting(self):
        """Test capacity endpoint when no setting exists (falls back to env default)."""
        # Don't create a setting, so it uses the default
        response = self.client.get("/executor/capacity/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("running", data)
        self.assertIn("max", data)
        # Default from settings.py or env is 10 or 3
        self.assertGreaterEqual(data["max"], 1)


class ExecutorReadsSettingTests(APITestCase):
    """Tests that the executor reads the stored setting each poll cycle."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.user = self.make_user()

    def test_executor_respects_stored_max_concurrency(self):
        """Test that poll_and_execute respects the stored max concurrency."""
        # Create a low max concurrency setting
        SystemSetting.objects.create(
            key="executor_max_concurrency",
            value=1,
        )

        # Create 3 in-progress tasks, all ready for execution
        task1 = self.make_task(self.board, status=TaskStatus.IN_PROGRESS, assignee=self.user)
        task2 = self.make_task(self.board, status=TaskStatus.IN_PROGRESS, assignee=self.user)
        task3 = self.make_task(self.board, status=TaskStatus.IN_PROGRESS, assignee=self.user)

        # Check logging output
        with self.assertLogs("taskit.dag_executor", level="DEBUG") as logs:
            poll_and_execute()

        log_content = "\n".join(logs.output)
        self.assertIn("available_slots=1", log_content)

    def test_executor_with_different_max_values(self):
        """Test executor with different max concurrency values."""
        board = self.make_board(name="Board2")
        user = self.make_user(name="Bob", email="bob@example.com")

        SystemSetting.objects.create(
            key="executor_max_concurrency",
            value=2,
        )

        # Create 3 executing tasks
        self.make_task(board, status=TaskStatus.EXECUTING)
        self.make_task(board, status=TaskStatus.EXECUTING)
        self.make_task(board, status=TaskStatus.EXECUTING)

        # Create 2 in-progress tasks
        task1 = self.make_task(board, status=TaskStatus.IN_PROGRESS, assignee=user)
        task2 = self.make_task(board, status=TaskStatus.IN_PROGRESS, assignee=user)

        with self.assertLogs("taskit.dag_executor", level="DEBUG") as logs:
            poll_and_execute()

        log_content = "\n".join(logs.output)
        # When 3 executing and max=2, no available slots message should appear
        self.assertIn("No available slots", log_content)

    def test_executor_updates_when_setting_changes(self):
        """Test that executor picks up changed setting on next poll."""
        setting = SystemSetting.objects.create(
            key="executor_max_concurrency",
            value=1,
        )

        with self.assertLogs("taskit.dag_executor", level="DEBUG") as logs1:
            poll_and_execute()
        log_content1 = "\n".join(logs1.output)
        self.assertIn("max=1", log_content1)

        # Change setting to 5
        setting.value = 5
        setting.save()

        # Next poll should use new value
        with self.assertLogs("taskit.dag_executor", level="DEBUG") as logs2:
            poll_and_execute()
        log_content2 = "\n".join(logs2.output)
        self.assertIn("max=5", log_content2)


class SetMaxConcurrencyEndpointTests(APITestCase):
    """Tests for setting max concurrency via API."""

    def setUp(self):
        super().setUp()
        self.client = APIClient()

    def test_set_max_concurrency_valid(self):
        """Test setting max concurrency with valid value."""
        response = self.client.post("/executor/max-concurrency/", {"value": 5})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["value"], 5)

        # Verify it was saved
        setting = SystemSetting.objects.get(key="executor_max_concurrency")
        self.assertEqual(setting.value, 5)

    def test_set_max_concurrency_invalid_zero(self):
        """Test that setting max concurrency to 0 is rejected."""
        response = self.client.post("/executor/max-concurrency/", {"value": 0})
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("error", data)

    def test_set_max_concurrency_invalid_negative(self):
        """Test that setting max concurrency to negative is rejected."""
        response = self.client.post("/executor/max-concurrency/", {"value": -1})
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("error", data)

    def test_set_max_concurrency_includes_suggestion(self):
        """Test that setting endpoint returns suggested max."""
        response = self.client.post("/executor/max-concurrency/", {"value": 2})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("suggested_max", data)
        self.assertGreaterEqual(data["suggested_max"], 1)

    def test_get_max_concurrency(self):
        """Test retrieving current max concurrency."""
        SystemSetting.objects.create(
            key="executor_max_concurrency",
            value=7,
        )

        response = self.client.get("/executor/max-concurrency/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["value"], 7)
        self.assertIn("suggested_max", data)
