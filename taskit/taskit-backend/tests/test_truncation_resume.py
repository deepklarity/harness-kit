"""Resume-on-truncation backend behaviour (task #332).

When a model hits its output cap mid-task with work in the worktree, the
orchestrator records a *resumable truncation*: status FAILED, failure_type
``truncation``, plus resume bookkeeping in the execution metadata. Three
backend pieces make the resume happen:

1. The failure tagger honors the explicit ``truncation`` type, so the
   truncation AUTO_REQUEUE policy (max_retries=2) requeues the SAME task into
   the SAME worktree — attempt n+1.
2. The execution_result view persists ``truncation_resume_count`` (a durable
   capability signal task #328's routing table reads) and
   ``truncation_resume_pending`` (which tells the next dispatch to rebuild a
   resume prompt), and clears pending the moment a run is no longer a
   resumable truncation.
3. The policy cap is the resume cap: after two auto-requeues the third
   truncation stays FAILED and routes to the policy (too big for the budget).

NonVisual — no browser/screenshots needed.
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from unittest.mock import patch

from .base import APITestCase
from tasks.failure_policy import POLICY_META_KEY, apply_failure_policy
from tasks.failure_tagger import classify_failure
from tasks.models import TaskStatus


class TestTaggerHonorsExplicitTruncation(APITestCase):
    """The evidence ladder names an output-cap truncation explicitly; the
    tagger must map that type to the truncation class regardless of the
    free-text reason, so the AUTO_REQUEUE policy fires."""

    def test_explicit_truncation_type_maps_to_truncation_class(self):
        cls = classify_failure({
            "last_failure_type": "truncation",
            "last_failure_reason": "resuming the same task in the same worktree",
        })
        self.assertEqual(cls, "truncation")

    def test_truncation_type_with_empty_reason_still_truncation(self):
        cls = classify_failure({"last_failure_type": "truncation", "last_failure_reason": ""})
        self.assertEqual(cls, "truncation")


class TestViewPersistsResumeMetadata(APITestCase):
    """The execution_result view must carry the resume bookkeeping onto the
    task so the next dispatch rebuilds a resume prompt and the routing table
    can see how many times the task ran out of output budget."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board, status="IN_PROGRESS")

    def _post_resumable_truncation(self, resume_count):
        return self.client.post(
            f"/tasks/{self.task.id}/execution_result/",
            {
                "execution_result": {
                    "success": False,
                    "raw_output": "",
                    "error": (
                        "Agent hit the provider output cap (truncated "
                        "mid-generation) with work in progress."
                    ),
                    "duration_ms": 900.0,
                    "agent": "claude",
                    "failure_type": "truncation",
                    "failure_reason": "output cap hit mid-task",
                    "failure_origin": "orchestrator:task_execution",
                    "metadata": {
                        "truncation_resume_count": resume_count,
                        "truncation_resume_pending": True,
                    },
                },
                "status": "FAILED",
                "updated_by": "claude+claude-opus@odin.agent",
            },
            format="json",
        )

    def test_resumable_truncation_persists_count_and_pending(self):
        resp = self._post_resumable_truncation(resume_count=1)
        self.assertEqual(resp.status_code, 200)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "FAILED")
        # Failure class is truncation → truncation AUTO_REQUEUE policy fires.
        self.assertEqual(self.task.metadata.get("failure_class"), "truncation")
        # Resume bookkeeping persisted.
        self.assertEqual(self.task.metadata.get("truncation_resume_count"), 1)
        self.assertTrue(self.task.metadata.get("truncation_resume_pending"))

    def test_success_clears_pending_but_keeps_count(self):
        # First a resumable truncation stamps count + pending.
        self._post_resumable_truncation(resume_count=1)
        self.task.refresh_from_db()
        self.assertTrue(self.task.metadata.get("truncation_resume_pending"))

        # The task is redispatched and this time succeeds.
        self.task.status = "IN_PROGRESS"
        self.task.save(update_fields=["status"])
        resp = self.client.post(
            f"/tasks/{self.task.id}/execution_result/",
            {
                "execution_result": {
                    "success": True,
                    "raw_output": "Finished the remaining work.\n\n-------ODIN-STATUS-------\nSUCCESS\n-------ODIN-SUMMARY-------\nDone.",
                    "error": None,
                    "duration_ms": 3000.0,
                    "agent": "claude",
                    "metadata": {},
                },
                "status": "REVIEW",
                "updated_by": "claude+claude-opus@odin.agent",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.task.refresh_from_db()
        # A successful run leaves FAILED behind (REVIEW, or auto-advanced past
        # it) — the point is it is no longer a resumable failure.
        self.assertNotEqual(self.task.status, "FAILED")
        # Pending is cleared — no stale resume prompt on a future run.
        self.assertNotIn("truncation_resume_pending", self.task.metadata)
        # But the count survives as a historical capability signal.
        self.assertEqual(self.task.metadata.get("truncation_resume_count"), 1)


class TestResumeCapRoutesToPolicy(APITestCase):
    """The truncation policy cap (max_retries=2) IS the resume cap: the third
    truncation is not requeued — it stays FAILED and routes to the policy."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board(allow_project_root_execution=True)
        self.assignee = self.make_user(name="claude", email="claude@odin.agent")

    def _failed_truncation(self, counter):
        return self.make_task(
            self.board,
            title="output-hungry task",
            status=TaskStatus.FAILED,
            assignee=self.assignee,
            model_name="claude-sonnet-4-5",
            metadata={
                "last_failure_type": "truncation",
                "last_failure_reason": "output cap hit mid-task",
                "last_failure_origin": "orchestrator:task_execution",
                "failure_class": "truncation",
                POLICY_META_KEY: counter,
                "truncation_resume_count": counter + 1,
            },
        )

    def test_first_two_truncations_auto_requeue(self):
        for counter in (0, 1):
            task = self._failed_truncation(counter=counter)
            with patch("tasks.dag_executor.execute_single_task"):
                requeued = apply_failure_policy(task)
            self.assertTrue(requeued, f"counter={counter} should auto-requeue")
            task.refresh_from_db()
            self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
            self.assertEqual(task.metadata[POLICY_META_KEY], counter + 1)

    def test_third_truncation_stays_failed_and_routes_to_policy(self):
        # Two resumes already happened (counter at the cap of 2).
        task = self._failed_truncation(counter=2)
        with patch("tasks.dag_executor.execute_single_task"):
            requeued = apply_failure_policy(task)
        self.assertFalse(requeued)
        task.refresh_from_db()
        # The task stays FAILED — the third truncation means it's too big for
        # the output budget; the routing policy takes over from here.
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertTrue(task.metadata.get("policy_cap_reached"))
        # The resume count is visible to task #328's routing table.
        self.assertEqual(task.metadata.get("truncation_resume_count"), 3)
