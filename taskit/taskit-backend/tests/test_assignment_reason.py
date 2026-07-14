"""Tests for the human-override flag on task.metadata.assignment_reason.

When a human changes the assignee (or model) after dispatch, the
assignment_reason dict must record that the router's pick was
overridden. The flag (`override=true`) and the author (`override_by`)
are stamped by `TaskViewSet.update` in the taskit-backend — never by
odin, since the router cannot override itself.

This is the safety net that keeps the WHY honest: if a human moved
the task to a different agent, the UI label switches from "Auto" to
"Override" and the reason string is preserved.
"""

from rest_framework.test import APIClient

from tasks.models import Board, Spec, Task, User
from tests.base import APITestCase


class TestAssignmentReasonOverride(APITestCase):
    """When PATCH /tasks/:id/ changes the assignee, the metadata
    `assignment_reason.override` must flip to True and
    `override_by` must record the requesting user."""

    def setUp(self):
        super().setUp()
        self.board = Board.objects.create(name="wave6")
        self.spec = Spec.objects.create(
            title="Surface routing WHY",
            source="spec.md",
            content="x",
            board=self.board,
            odin_id="sp_234",
        )
        # Seeded task with the dispatch-time dict (router picked qwen).
        self.task = Task.objects.create(
            title="Implement WHY line",
            description="x",
            board=self.board,
            spec=self.spec,
            created_by="odin@example.com",
            metadata={
                "assignment_reason": {
                    "agent": "qwen",
                    "model": "qwen-coder",
                    "rule": "history",
                    "reason": "Routed to qwen/qwen-coder (history-driven).",
                    "override": False,
                    "cheaper_alternatives": [],
                    "twin_consensus": None,
                }
            },
        )
        self.client = APIClient()

    def test_initial_assignment_reason_has_override_false(self):
        """Sanity: dispatch-time flag is False."""
        ar = self.task.metadata["assignment_reason"]
        self.assertEqual(ar["override"], False)
        self.assertNotIn("override_by", ar)

    def test_assignee_change_flips_override_to_true(self):
        """PATCH assignee_id → assignment_reason.override=true +
        override_by=<author>."""
        new_agent = User.objects.create(
            name="claude",
            email="claude@example.com",
        )
        url = f"/tasks/{self.task.id}/"
        resp = self.client.patch(
            url,
            {
                "assignee_id": new_agent.id,
                "model_name": "claude-sonnet-4-5",
                "updated_by": "alice@example.com",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.task.refresh_from_db()
        ar = self.task.metadata.get("assignment_reason")
        self.assertIsNotNone(ar, "assignment_reason must survive the update")
        self.assertEqual(ar["override"], True)
        # The author must be recorded — otherwise the audit trail is a
        # blank.
        self.assertIn("override_by", ar)
        self.assertTrue(ar["override_by"])
        # The picked agent should be reflected in the dict (so the UI
        # can show the new agent without an extra fetch).
        self.assertEqual(ar["agent"], new_agent.name)
        self.assertEqual(ar["model"], "claude-sonnet-4-5")

    def test_machine_actor_routing_sync_does_not_flip_override(self):
        """Odin PATCHes assignee/model right after planning to sync its
        own routing decision. A machine actor (@odin.agent, @harness.kit,
        @system, @taskit) must never render as a human 'Override'."""
        new_agent = User.objects.create(
            name="claude",
            email="claude@odin.agent",
        )
        url = f"/tasks/{self.task.id}/"
        resp = self.client.patch(
            url,
            {
                "assignee_id": new_agent.id,
                "model_name": "claude-sonnet-5",
                "updated_by": "odin@harness.kit",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.task.refresh_from_db()
        ar = self.task.metadata.get("assignment_reason")
        self.assertEqual(ar["override"], False)
        self.assertNotIn("override_by", ar)

    def test_non_routing_edit_leaves_override_false(self):
        """A non-routing edit (e.g. title) does NOT flip the override
        flag — that flag is reserved for assignee/model overrides."""
        url = f"/tasks/{self.task.id}/"
        resp = self.client.patch(
            url,
            {"title": "renamed", "updated_by": "alice@example.com"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.task.refresh_from_db()
        ar = self.task.metadata.get("assignment_reason")
        self.assertEqual(ar["override"], False)
        self.assertNotIn("override_by", ar)

    def test_assignment_reason_absent_after_update_is_backfilled(self):
        """A task that pre-dates W6.14 (no assignment_reason) must not
        break when the assignee is changed. The override flag is
        stamped on the new dict anyway."""
        old = Task.objects.create(
            title="old task",
            description="x",
            board=self.board,
            spec=self.spec,
            created_by="odin@example.com",
            metadata={},  # pre-W6.14
        )
        new_agent = User.objects.create(
            name="gemini",
            email="gemini@example.com",
        )
        url = f"/tasks/{old.id}/"
        resp = self.client.patch(
            url,
            {
                "assignee_id": new_agent.id,
                "model_name": "gemini-2.5-flash",
                "updated_by": "bob@example.com",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        old.refresh_from_db()
        ar = old.metadata.get("assignment_reason")
        self.assertIsNotNone(ar)
        self.assertEqual(ar["override"], True)
        self.assertEqual(ar["agent"], "gemini")
        self.assertEqual(ar["override_by"], "bob@example.com")