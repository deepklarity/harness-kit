from unittest.mock import patch

from .base import APITestCase
from tasks.models import UserSetting


class TestUserIdeSettings(APITestCase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user(name="Admin", email="admin@example.com", is_admin=True)

    @patch("tasks.views.detect_supported_ides")
    def test_save_preferred_ide_requires_detected_ide(self, mock_detect):
        mock_detect.return_value = []
        resp = self.client.patch(
            "/api/user-settings/ide/",
            {"preferred_ide_id": "cursor"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("preferred_ide_id", resp.data)

    @patch("tasks.views.detect_supported_ides")
    def test_save_and_read_preferred_ide(self, mock_detect):
        mock_detect.return_value = [
            type("Ide", (), {"id": "cursor", "label": "Cursor", "icon_key": "cursor"})()
        ]
        save = self.client.patch(
            "/api/user-settings/ide/",
            {"preferred_ide_id": "cursor"},
            format="json",
        )
        self.assertEqual(save.status_code, 200)
        self.assertEqual(save.data["preferred_ide_id"], "cursor")

        settings_obj = UserSetting.objects.get(user=self.user)
        self.assertEqual(settings_obj.preferred_ide_id, "cursor")

        fetch = self.client.get("/api/user-settings/ide/")
        self.assertEqual(fetch.status_code, 200)
        self.assertEqual(fetch.data["preferred_ide_id"], "cursor")


class TestTaskOpenProject(APITestCase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user(name="Admin", email="admin@example.com", is_admin=True)
        self.board = self.make_board(working_dir="/tmp/project-root")
        self.task = self.make_task(self.board, status="DONE")

    @patch("tasks.views.detect_supported_ides")
    def test_task_ide_options_include_detected_and_preference(self, mock_detect):
        UserSetting.objects.create(user=self.user, preferred_ide_id="cursor")
        mock_detect.return_value = [
            type("Ide", (), {"id": "cursor", "label": "Cursor", "icon_key": "cursor"})()
        ]
        resp = self.client.get(f"/api/tasks/{self.task.id}/ide-options/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["project_root"], "/tmp/project-root")
        self.assertEqual(resp.data["preferred_ide_id"], "cursor")
        self.assertEqual(resp.data["detected_ides"][0]["id"], "cursor")

    def test_open_project_requires_preference(self):
        resp = self.client.post(f"/api/tasks/{self.task.id}/open-project/", {}, format="json")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.data["code"], "no_preferred_ide")

    @patch("tasks.views.get_supported_ide")
    def test_open_project_detects_missing_preferred_ide(self, mock_get_ide):
        UserSetting.objects.create(user=self.user, preferred_ide_id="cursor")
        mock_get_ide.return_value = type(
            "SupportedIde",
            (),
            {"detect_launcher": lambda self: None, "label": "Cursor"},
        )()
        resp = self.client.post(f"/api/tasks/{self.task.id}/open-project/", {}, format="json")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.data["code"], "preferred_ide_not_detected")

    @patch("tasks.views.get_supported_ide")
    def test_open_project_launches_configured_ide(self, mock_get_ide):
        UserSetting.objects.create(user=self.user, preferred_ide_id="cursor")

        class FakeIde:
            id = "cursor"
            label = "Cursor"

            def detect_launcher(self):
                return ("cli", "/usr/bin/cursor")

            def launch(self, project_root):
                self.launched_root = project_root

        fake = FakeIde()
        mock_get_ide.return_value = fake
        resp = self.client.post(f"/api/tasks/{self.task.id}/open-project/", {}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["ok"])
        self.assertEqual(resp.data["project_root"], "/tmp/project-root")
        self.assertEqual(fake.launched_root, "/tmp/project-root")
