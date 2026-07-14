"""Tests for POST /tasks/:id/rework/ — compose a follow-up task from a
parent task + a one-sentence human instruction (task #259).

No model call: this is pure assembly. The new task's description is built
from (a) the parent's context — title, its WHY section parsed out of its
description, and its proof pointer, (b) the instruction verbatim as the
requirement, and (c) the standard working-protocol footer. Twins/quote and
agent routing are NOT re-implemented here — they already fire automatically
once a task exists (tasks/similarity.py, tasks/estimation.py, the odin
orchestrator) — this endpoint only needs to create a normal Task row with
the right board/spec/metadata so that existing machinery picks it up.

Scenario matrix:
  - happy path: composes title + description + parent-linkage metadata,
    inherits board (+ spec when the parent has one), records a "created"
    TaskHistory row, status TODO, no assignee (routing picks it later).
  - WHY-section extraction: present → included; absent → composition still
    succeeds (no crash, no fabricated WHY).
  - proof pointer + working-protocol footer are present in the composed
    description, and the footer's proof path points at the NEW task's id,
    not the parent's.
  - guard rails: missing/blank instruction, under 10 chars, single-word
    (no whitespace) instruction, missing created_by — all 400, no task
    created.
  - unknown parent id → 404.
  - reworking the same parent twice creates two independent follow-up
    tasks (no dedup).
"""

from rest_framework.test import APIClient

from tasks.models import Board, Spec, Task, TaskHistory, TaskStatus
from tests.base import APITestCase


class TestReworkComposition(APITestCase):
    """Happy-path composition: title, description, board/spec inheritance."""

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.board = Board.objects.create(name="wave9")
        self.spec = Spec.objects.create(
            board=self.board, odin_id="sp_259", title="Rework spec", source="spec.md", content="x",
        )
        self.parent = Task.objects.create(
            board=self.board,
            spec=self.spec,
            title="Mission control v1 — the inbox",
            description=(
                "WHY (Moonshots bucket, 2/10 — user priority track): the flow "
                "works; the interfaces must amplify. Mission control v1 is a "
                "floor.\n\n"
                "## Scope\n"
                "1. Backend: build the inbox endpoint.\n"
            ),
            status=TaskStatus.TESTING,
            created_by="odin@example.com",
        )

    def _rework(self, instruction, **extra):
        body = {"instruction": instruction, "created_by": "alice@test.com", **extra}
        return self.client.post(f"/tasks/{self.parent.id}/rework/", body, format="json")

    def test_happy_path_creates_new_task(self):
        resp = self._rework("Add a dark-mode toggle to the settings page.")
        self.assertEqual(resp.status_code, 201, resp.data)
        new_id = resp.data["id"]
        self.assertNotEqual(str(new_id), str(self.parent.id))
        new_task = Task.objects.get(pk=new_id)
        self.assertEqual(new_task.status, TaskStatus.TODO)
        self.assertIsNone(new_task.assignee_id)
        self.assertEqual(new_task.created_by, "alice@test.com")

    def test_new_task_inherits_board_and_spec(self):
        resp = self._rework("Add a dark-mode toggle to the settings page.")
        new_task = Task.objects.get(pk=resp.data["id"])
        self.assertEqual(new_task.board_id, self.board.id)
        self.assertEqual(new_task.spec_id, self.spec.id)

    def test_metadata_links_parent(self):
        resp = self._rework("Add a dark-mode toggle to the settings page.")
        new_task = Task.objects.get(pk=resp.data["id"])
        self.assertEqual(new_task.metadata.get("parent_task_id"), self.parent.id)
        self.assertEqual(
            new_task.metadata.get("rework_instruction"),
            "Add a dark-mode toggle to the settings page.",
        )
        self.assertEqual(new_task.metadata.get("created_via"), "rework")

    def test_title_derived_from_instruction(self):
        resp = self._rework("Add a dark-mode toggle to the settings page.")
        new_task = Task.objects.get(pk=resp.data["id"])
        self.assertIn("dark-mode toggle", new_task.title)
        self.assertLessEqual(len(new_task.title), 255)

    def test_description_includes_why_section_from_parent(self):
        resp = self._rework("Add a dark-mode toggle to the settings page.")
        new_task = Task.objects.get(pk=resp.data["id"])
        self.assertIn("the flow", new_task.description)
        self.assertIn("Mission control v1 is a floor", new_task.description)

    def test_description_includes_instruction_verbatim(self):
        instruction = "Add a dark-mode toggle to the settings page."
        resp = self._rework(instruction)
        new_task = Task.objects.get(pk=resp.data["id"])
        self.assertIn(instruction, new_task.description)

    def test_description_includes_parent_proof_pointer(self):
        resp = self._rework("Add a dark-mode toggle to the settings page.")
        new_task = Task.objects.get(pk=resp.data["id"])
        self.assertIn(f".proof/task-{self.parent.id}/proof.md", new_task.description)

    def test_description_includes_working_protocol_footer_for_new_task(self):
        resp = self._rework("Add a dark-mode toggle to the settings page.")
        new_task = Task.objects.get(pk=resp.data["id"])
        self.assertIn("Working protocol", new_task.description)
        # The footer's proof path must point at the NEW task, not the parent.
        self.assertIn(f".proof/task-{new_task.id}/proof.md", new_task.description)

    def test_creation_history_recorded(self):
        resp = self._rework("Add a dark-mode toggle to the settings page.")
        new_task_id = resp.data["id"]
        history = TaskHistory.objects.filter(task_id=new_task_id, field_name="created")
        self.assertTrue(history.exists())

    def test_rework_without_why_section_still_succeeds(self):
        plain_parent = Task.objects.create(
            board=self.board, title="Plain task", description="Just a plain description, no WHY.",
            status=TaskStatus.TESTING, created_by="odin@example.com",
        )
        resp = self.client.post(
            f"/tasks/{plain_parent.id}/rework/",
            {"instruction": "Fix the flaky timeout in the upload test.", "created_by": "alice@test.com"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)

    def test_rework_with_empty_parent_description_still_succeeds(self):
        empty_parent = Task.objects.create(
            board=self.board, title="Empty description task", description="",
            status=TaskStatus.TESTING, created_by="odin@example.com",
        )
        resp = self.client.post(
            f"/tasks/{empty_parent.id}/rework/",
            {"instruction": "Fix the flaky timeout in the upload test.", "created_by": "alice@test.com"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)

    def test_reworking_same_parent_twice_creates_two_tasks(self):
        resp1 = self._rework("Add a dark-mode toggle to the settings page.")
        resp2 = self._rework("Also add a light-mode toggle for good measure.")
        self.assertEqual(resp1.status_code, 201)
        self.assertEqual(resp2.status_code, 201)
        self.assertNotEqual(resp1.data["id"], resp2.data["id"])


class TestReworkGuardRails(APITestCase):
    """Fast-path guard rails: this is not a chat, so ambiguous or too-short
    instructions are rejected with a 400 and a helpful message instead of
    silently producing a low-quality brief."""

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.board = Board.objects.create(name="wave9")
        self.parent = Task.objects.create(
            board=self.board, title="Parent task", description="Some description.",
            status=TaskStatus.TESTING, created_by="odin@example.com",
        )

    def _task_count(self):
        return Task.objects.count()

    def test_missing_instruction_returns_400(self):
        before = self._task_count()
        resp = self.client.post(
            f"/tasks/{self.parent.id}/rework/", {"created_by": "alice@test.com"}, format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self._task_count(), before)

    def test_blank_instruction_returns_400(self):
        before = self._task_count()
        resp = self.client.post(
            f"/tasks/{self.parent.id}/rework/",
            {"instruction": "   ", "created_by": "alice@test.com"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self._task_count(), before)

    def test_instruction_under_ten_chars_returns_400(self):
        before = self._task_count()
        resp = self.client.post(
            f"/tasks/{self.parent.id}/rework/",
            {"instruction": "fix it", "created_by": "alice@test.com"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        message = str(resp.data)
        self.assertIn("10", message)  # helpful message names the minimum length
        self.assertEqual(self._task_count(), before)

    def test_single_word_instruction_returns_400(self):
        before = self._task_count()
        resp = self.client.post(
            f"/tasks/{self.parent.id}/rework/",
            {"instruction": "reformatthecodebase", "created_by": "alice@test.com"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self._task_count(), before)

    def test_missing_created_by_returns_400(self):
        before = self._task_count()
        resp = self.client.post(
            f"/tasks/{self.parent.id}/rework/",
            {"instruction": "Add a dark-mode toggle to the settings page."},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self._task_count(), before)

    def test_unknown_parent_returns_404(self):
        resp = self.client.post(
            "/tasks/999999/rework/",
            {"instruction": "Add a dark-mode toggle to the settings page.", "created_by": "alice@test.com"},
            format="json",
        )
        self.assertEqual(resp.status_code, 404)
