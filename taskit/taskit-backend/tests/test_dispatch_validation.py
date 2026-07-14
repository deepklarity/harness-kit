"""Tests for the dispatch-readiness gate: a task with no spec, on a board that
has not opted into project-root execution, must be refused at PATCH/POST time
rather than being allowed to dispatch and fail downstream in the executor.

See tasks/dag_executor.py poll_and_execute (~line 406-457) for the last-resort
downstream guard this gate front-runs — that code stays as a safety net for
tasks that reach IN_PROGRESS through some other path (e.g. schedules), but the
API layer should never let a normal user PATCH into that failure mode.
"""

from tests.base import APITestCase


class DispatchValidationTests(APITestCase):
    NO_SPEC_MESSAGE_FRAGMENT = "no spec"

    def test_patch_to_in_progress_without_spec_and_no_optin_is_rejected(self):
        board = self.make_board(allow_project_root_execution=False)
        task = self.make_task(board, status="TODO")

        resp = self.client.patch(
            f"/tasks/{task.id}/",
            {"status": "IN_PROGRESS", "updated_by": "alice@test.com"},
            format="json",
        )

        self.assertEqual(resp.status_code, 400, resp.data)
        detail = str(resp.data)
        self.assertIn("spec", detail.lower())
        task.refresh_from_db()
        self.assertEqual(task.status, "TODO")

    def test_patch_to_in_progress_without_spec_but_board_opted_in_is_allowed(self):
        board = self.make_board(allow_project_root_execution=True)
        task = self.make_task(board, status="TODO")

        resp = self.client.patch(
            f"/tasks/{task.id}/",
            {"status": "IN_PROGRESS", "updated_by": "alice@test.com"},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        task.refresh_from_db()
        self.assertEqual(task.status, "IN_PROGRESS")

    def test_patch_to_in_progress_with_spec_is_allowed(self):
        board = self.make_board(allow_project_root_execution=False)
        spec = self.make_spec(board)
        task = self.make_task(board, status="TODO", spec=spec)

        resp = self.client.patch(
            f"/tasks/{task.id}/",
            {"status": "IN_PROGRESS", "updated_by": "alice@test.com"},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        task.refresh_from_db()
        self.assertEqual(task.status, "IN_PROGRESS")

    def test_create_directly_in_progress_without_spec_is_rejected(self):
        board = self.make_board(allow_project_root_execution=False)

        resp = self.client.post(
            "/tasks/",
            {
                "board_id": board.id,
                "title": "Direct dispatch attempt",
                "created_by": "alice@test.com",
                "status": "IN_PROGRESS",
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 400, resp.data)
        detail = str(resp.data)
        self.assertIn("spec", detail.lower())

    def test_create_directly_in_progress_with_optin_board_is_allowed(self):
        board = self.make_board(allow_project_root_execution=True)

        resp = self.client.post(
            "/tasks/",
            {
                "board_id": board.id,
                "title": "Direct dispatch attempt",
                "created_by": "alice@test.com",
                "status": "IN_PROGRESS",
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 201, resp.data)

    def test_title_only_edit_on_specless_task_is_unaffected(self):
        board = self.make_board(allow_project_root_execution=False)
        task = self.make_task(board, status="TODO")

        resp = self.client.patch(
            f"/tasks/{task.id}/",
            {"title": "Renamed", "updated_by": "alice@test.com"},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        task.refresh_from_db()
        self.assertEqual(task.title, "Renamed")

    def test_non_dispatch_status_edit_on_specless_task_is_unaffected(self):
        board = self.make_board(allow_project_root_execution=False)
        task = self.make_task(board, status="TODO")

        resp = self.client.patch(
            f"/tasks/{task.id}/",
            {"status": "BACKLOG", "updated_by": "alice@test.com"},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        task.refresh_from_db()
        self.assertEqual(task.status, "BACKLOG")

    def test_patching_task_already_in_progress_without_status_change_is_unaffected(self):
        # Task somehow already IN_PROGRESS (e.g. legacy data) with no spec —
        # editing an unrelated field must not be blocked retroactively; the
        # gate only fires on a *transition into* IN_PROGRESS.
        board = self.make_board(allow_project_root_execution=True)
        task = self.make_task(board, status="IN_PROGRESS")
        board.allow_project_root_execution = False
        board.save()

        resp = self.client.patch(
            f"/tasks/{task.id}/",
            {"priority": "HIGH", "updated_by": "alice@test.com"},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
