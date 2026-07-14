"""Tests for the spec-branch verify gate (W5 — task #208).

Tags: [mock] — uses Django SQLite + mocked verify.sh runs.

Background
----------
A clean merge after a passed review IS done — but two individually-green
tasks can still break each other after merging (live case: two tasks
minted the same Django migration number; each branch was green, the
combination broke the suite).  With promote-check retired (W5 task #205),
this gate is the only place suites still run after a merge: once per
landed merge, against the spec branch in a fresh temp worktree, posted
async so it doesn't block the next merge.

Contract:

  - After each successful task merge into a spec branch, run
    ``scripts/verify.sh`` against the spec branch HEAD.  The run lives
    in a fresh temp worktree — never the operator's main checkout —
    in a background thread; the next merge proceeds immediately.

  - **GREEN** (every selected suite passes): at most one TaskComment
    (the timing line).  No SpecComment — green merges produce no noise.

  - **RED** (any suite fails or goes env-missing): a SpecComment names
    the failing suite(s), the merge task/branch/SHA that turned it
    red, and the log path.  ``spec.metadata.verify_status`` is set to
    ``"red"`` so the operator sees it on the board.  The merge is
    NOT auto-reverted — the spec branch stays at the failed HEAD
    until the operator decides.

  - Coalesce: concurrent merges onto the same spec only trigger ONE
    verify run, against the latest HEAD seen.
"""

import os
import shutil
import tempfile
import threading
import time

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from unittest.mock import patch

from tasks.models import CommentType, SpecComment, TaskComment, TaskStatus
from tests.base import APITestCase


class _SpecVerifyCase(APITestCase):
    """Shared fixtures: a board, a spec with a branch, a task in REVIEW."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.spec = self.make_spec(
            self.board,
            odin_id="sp_fable_w5",
            metadata={"branch": "spec/sp_fable_w5"},
        )
        self.task = self.make_task(
            self.board,
            spec=self.spec,
            status=TaskStatus.REVIEW,
            metadata={"branch": "task/sp_fable_w5/210"},
        )

    def tearDown(self):
        # Make sure no background threads leak across tests — the
        # coordinator is process-global, so a verified run started by
        # one test could collide with the next's state if we let it.
        from tasks import spec_verify

        spec_verify._coordinator._reset_for_tests()
        super().tearDown()

    def _make_report(self, *, status, suites=None, log_path="/tmp/fake.log", duration_s=12.3):
        from tasks.spec_verify import VerifyReport

        return VerifyReport(
            head_sha="abc1234",
            spec_branch="spec/sp_fable_w5",
            status=status,
            suites=suites or [],
            log_path=log_path,
            duration_s=duration_s,
            run_at=time.time(),
        )


class RedRunCommentsAndFlagsTests(_SpecVerifyCase):
    """RED verify → spec-level comment + metadata flag.  No auto-revert."""

    def test_red_verify_posts_spec_comment_naming_culprit_merge(self):
        """A RED run posts one spec comment naming the failing suite, the
        merge task, the branch, the SHA, and the log path.  It must NOT
        flip the spec back to the pre-merge HEAD."""
        from tasks.spec_verify import VerifyReport, run_spec_verify_blocking

        report = VerifyReport(
            head_sha="deadbeef",
            spec_branch="spec/sp_fable_w5",
            status="red",
            suites=[
                {"name": "backend", "verdict": "FAIL", "tests": "1 failed, 38 passed"},
                {"name": "snapshots", "verdict": "FAIL", "tests": "2 failed, 5 passed"},
            ],
            log_path="/tmp/spec-verify-sp_fable_w5.log",
            duration_s=42.7,
            run_at=time.time(),
        )
        with patch("tasks.spec_verify._execute_verify", return_value=report):
            with patch(
                "tasks.spec_verify._merge_task_id_for_sha",
                return_value=self.task.id,
            ):
                run_spec_verify_blocking(
                    spec_id="sp_fable_w5",
                    head_sha="deadbeef",
                    merge_branch="task/sp_fable_w5/210",
                )

        spec_comments = list(SpecComment.objects.filter(spec=self.spec))
        self.assertEqual(len(spec_comments), 1)
        body = spec_comments[0].content

        # Names the merge that turned the spec red.
        self.assertIn("task/sp_fable_w5/210", body)
        self.assertIn("210", body)
        self.assertIn("deadbeef", body)
        # Names every failing suite (so the operator sees the defect
        # from the board, not by digging through logs).
        self.assertIn("backend", body)
        self.assertIn("snapshots", body)
        self.assertIn("FAIL", body)
        # Points at the run log for diagnosis.
        self.assertIn("/tmp/spec-verify-sp_fable_w5.log", body)
        self.assertEqual(spec_comments[0].comment_type, CommentType.STATUS_UPDATE)

        # Spec metadata flags the red run — operator sees it on the board.
        self.spec.refresh_from_db()
        meta = self.spec.metadata or {}
        self.assertEqual(meta.get("verify_status"), "red")
        self.assertEqual(meta.get("verify_failed_suites"), ["backend", "snapshots"])
        self.assertEqual(meta.get("verify_offending_sha"), "deadbeef")

        # The merge is NOT auto-reverted.
        self.spec.refresh_from_db()
        self.assertEqual(meta["branch"], "spec/sp_fable_w5")
        self.assertFalse(meta.get("verify_reverted", False))

    def test_env_missing_is_treated_as_red(self):
        """An ``env_missing`` run is failure-shaped — gate still flags it
        so the operator knows the suite didn't actually run (the gate
        never silently says GREEN when a suite didn't run)."""
        from tasks.spec_verify import VerifyReport, run_spec_verify_blocking

        report = VerifyReport(
            head_sha="cafef00d",
            spec_branch="spec/sp_fable_w5",
            status="env_missing",
            suites=[
                {"name": "frontend", "verdict": "ENV_MISSING",
                 "tests": "vitest: not found"},
            ],
            log_path="/tmp/spec-verify-frontend.log",
            duration_s=8.1,
            run_at=time.time(),
        )
        with patch("tasks.spec_verify._execute_verify", return_value=report), \
             patch("tasks.spec_verify._merge_task_id_for_sha", return_value=self.task.id):
            run_spec_verify_blocking(
                spec_id="sp_fable_w5", head_sha="cafef00d",
                merge_branch="task/sp_fable_w5/210",
            )

        spec_comments = list(SpecComment.objects.filter(spec=self.spec))
        self.assertEqual(len(spec_comments), 1)
        body = spec_comments[0].content
        self.assertIn("frontend", body)
        self.assertIn("ENV_MISSING", body)

        self.spec.refresh_from_db()
        meta = self.spec.metadata or {}
        self.assertEqual(meta.get("verify_status"), "red")


class GreenRunStaysQuietTests(_SpecVerifyCase):
    """GREEN verify → no spec-level noise.  At most a one-line task note."""

    def test_green_verify_posts_no_spec_comment(self):
        """A GREEN run is silent at the spec level — no SpecComment."""
        from tasks.spec_verify import VerifyReport, run_spec_verify_blocking

        report = VerifyReport(
            head_sha="abc1234",
            spec_branch="spec/sp_fable_w5",
            status="green",
            suites=[
                {"name": "odin", "verdict": "PASS", "tests": "38 passed"},
                {"name": "backend", "verdict": "PASS", "tests": "57 passed"},
                {"name": "frontend", "verdict": "PASS", "tests": "141 passed"},
                {"name": "snapshots", "verdict": "PASS", "tests": "9 passed"},
            ],
            log_path="/tmp/spec-verify-green.log",
            duration_s=63.4,
            run_at=time.time(),
        )
        with patch("tasks.spec_verify._execute_verify", return_value=report), \
             patch("tasks.spec_verify._merge_task_id_for_sha", return_value=self.task.id):
            run_spec_verify_blocking(
                spec_id="sp_fable_w5", head_sha="abc1234",
                merge_branch="task/sp_fable_w5/210",
            )

        self.assertFalse(
            SpecComment.objects.filter(spec=self.spec).exists(),
            "GREEN verify must not produce a spec-level comment",
        )

    def test_green_verify_writes_no_red_flag(self):
        """GREEN → spec.metadata has no verify_status=red."""
        from tasks.spec_verify import VerifyReport, run_spec_verify_blocking

        report = VerifyReport(
            head_sha="abc1234", spec_branch="spec/sp_fable_w5",
            status="green", suites=[{"name": "odin", "verdict": "PASS"}],
            log_path="/tmp/log", duration_s=10.0, run_at=time.time(),
        )
        with patch("tasks.spec_verify._execute_verify", return_value=report), \
             patch("tasks.spec_verify._merge_task_id_for_sha", return_value=self.task.id):
            run_spec_verify_blocking(
                spec_id="sp_fable_w5", head_sha="abc1234",
                merge_branch="task/sp_fable_w5/210",
            )

        self.spec.refresh_from_db()
        meta = self.spec.metadata or {}
        self.assertNotEqual(meta.get("verify_status"), "red")
        self.assertEqual(meta.get("verify_status"), "green")
        self.assertEqual(meta.get("verify_head_sha"), "abc1234")


class CoalescingTests(_SpecVerifyCase):
    """Concurrent merges coalesce; only one verify runs, against latest HEAD."""

    def test_concurrent_enqueues_run_one_verifier(self):
        """Two enqueues within the running window coalesce → one run."""
        from tasks import spec_verify

        run_count = {"n": 0}
        run_log = []
        start_event = threading.Event()

        def fake_execute(spec_id, head_sha, merge_branch, base_dir=None):
            run_count["n"] += 1
            run_log.append(head_sha)
            start_event.set()
            # Hold the "run" until the test releases it.
            time.sleep(0.5)
            from tasks.spec_verify import VerifyReport
            return VerifyReport(
                head_sha=head_sha, spec_branch=f"spec/{spec_id}",
                status="green", suites=[],
                log_path="/tmp/log", duration_s=0.5, run_at=time.time(),
            )

        with patch("tasks.spec_verify._execute_verify", side_effect=fake_execute), \
             patch("tasks.spec_verify._merge_task_id_for_sha", return_value=self.task.id):
            # First enqueue starts the runner.
            spec_verify.enqueue_spec_verify(
                spec_id="sp_fable_w5",
                head_sha="sha-A",
                merge_branch="task/sp_fable_w5/210",
            )
            # Wait until the runner is actually inside _execute_verify —
            # without this, the second enqueue could race ahead of the
            # ``_start`` lock and start its own runner.
            start_event.wait(timeout=5)
            # Second enqueue lands while the first is mid-run.
            spec_verify.enqueue_spec_verify(
                spec_id="sp_fable_w5",
                head_sha="sha-B",
                merge_branch="task/sp_fable_w5/211",
            )

            # Let the first run complete and observe that the trailing
            # SHA triggers one more run, then idle.
            deadline = time.time() + 5
            while time.time() < deadline:
                state = spec_verify._coordinator._state_for_tests("sp_fable_w5")
                if not state["running"] and state["last_verified_head"] == "sha-B":
                    break
                time.sleep(0.05)

        # We expect: run-A, run-B.  No third run for sha-A after B
        # landed; the trailing loop sees B as the final pending head
        # and stops.
        self.assertEqual(run_count["n"], 2, f"expected 2 runs, got {run_count['n']}; log={run_log}")
        self.assertEqual(run_log[0], "sha-A")
        self.assertEqual(run_log[1], "sha-B")

    def test_three_rapid_enqueues_run_two_verifiers(self):
        """Three enqueues within the same window → first run + one trailing
        run for the latest pending HEAD.  The two stale SHAs in the middle
        never trigger a verify run each."""
        from tasks import spec_verify

        executed = []
        start_event = threading.Event()

        def fake_execute(spec_id, head_sha, merge_branch, base_dir=None):
            executed.append(head_sha)
            if not start_event.is_set():
                start_event.set()
            time.sleep(0.3)
            from tasks.spec_verify import VerifyReport
            return VerifyReport(
                head_sha=head_sha, spec_branch=f"spec/{spec_id}",
                status="green", suites=[],
                log_path="/tmp/log", duration_s=0.3, run_at=time.time(),
            )

        with patch("tasks.spec_verify._execute_verify", side_effect=fake_execute), \
             patch("tasks.spec_verify._merge_task_id_for_sha", return_value=self.task.id):
            spec_verify.enqueue_spec_verify(
                spec_id="sp_fable_w5",
                head_sha="sha-A",
                merge_branch="task/sp_fable_w5/210",
            )
            start_event.wait(timeout=5)

            # Land two more merges while A is still running.
            spec_verify.enqueue_spec_verify(
                spec_id="sp_fable_w5",
                head_sha="sha-B",
                merge_branch="task/sp_fable_w5/211",
            )
            spec_verify.enqueue_spec_verify(
                spec_id="sp_fable_w5",
                head_sha="sha-C",
                merge_branch="task/sp_fable_w5/212",
            )

            deadline = time.time() + 5
            while time.time() < deadline:
                state = spec_verify._coordinator._state_for_tests("sp_fable_w5")
                if not state["running"] and state["last_verified_head"] == "sha-C":
                    break
                time.sleep(0.05)

        # First run is A, the trailing loop sees C (not B) and runs once.
        self.assertEqual(executed, ["sha-A", "sha-C"], f"expected two runs total, got {executed}")


class HookFromMergeTaskOnReflectionTests(_SpecVerifyCase):
    """The merge path enqueues the verify gate after a successful merge."""

    def test_successful_merge_enqueues_verify(self):
        """A clean merge → enqueue_spec_verify is called with the spec
        branch HEAD."""
        from odin.worktree import MergeResult
        from tasks import spec_verify
        from tasks.dag_executor import merge_task_on_reflection

        merge_result = MergeResult(success=True)
        # Spec branch now points at the new HEAD; report a SHA so the
        # hook has something concrete to pass through.
        with patch("tasks.dag_executor._merge_task_branch", return_value=merge_result), \
             patch.object(spec_verify, "enqueue_spec_verify") as mock_enqueue, \
             patch(
                 "tasks.spec_verify._spec_branch_head_sha",
                 return_value="newheaddd",
             ):
            merge_task_on_reflection.__wrapped__(self.task.id)

        mock_enqueue.assert_called_once()
        args, kwargs = mock_enqueue.call_args
        # kwargs carries the names; positional args dict contains the spec id etc.
        params = dict(kwargs)
        # Identifier is the spec odin_id, not the DB pk.
        self.assertEqual(params.get("spec_id"), "sp_fable_w5")
        self.assertEqual(params.get("head_sha"), "newheaddd")
        # The branch that landed is recorded so a red-run report can
        # name the merging task by its branch path.
        self.assertEqual(params.get("merge_branch"), "task/sp_fable_w5/210")

    def test_noop_merge_does_not_enqueue(self):
        """``noop`` merge (branch already up-to-date) → no verify enqueued."""
        from odin.worktree import MergeResult
        from tasks import spec_verify
        from tasks.dag_executor import merge_task_on_reflection

        merge_result = MergeResult(success=True, noop=True)
        with patch("tasks.dag_executor._merge_task_branch", return_value=merge_result), \
             patch.object(spec_verify, "enqueue_spec_verify") as mock_enqueue:
            merge_task_on_reflection.__wrapped__(self.task.id)

        mock_enqueue.assert_not_called()

    def test_conflict_does_not_enqueue(self):
        """A conflicted merge does NOT enqueue — the merge itself was
        aborted, the spec branch HEAD is unchanged, nothing to verify."""
        from odin.worktree import MergeResult
        from tasks import spec_verify
        from tasks.dag_executor import merge_task_on_reflection

        merge_result = MergeResult(success=False, conflict=True, error="CONFLICT")
        with patch("tasks.dag_executor._merge_task_branch", return_value=merge_result), \
             patch.object(spec_verify, "enqueue_spec_verify") as mock_enqueue:
            merge_task_on_reflection.__wrapped__(self.task.id)

        mock_enqueue.assert_not_called()

    def test_run_in_fresh_temp_worktree_not_main_checkout(self):
        """``_execute_verify`` runs against a temp worktree, not the
        operator's main checkout.  The base_dir argument must NOT be the
        project_root."""
        from tasks.spec_verify import VerifyReport

        captured = {}
        captured["existed_in_fake"] = False

        def fake_execute(spec_id, head_sha, merge_branch, base_dir=None):
            captured["base_dir"] = base_dir
            # The contract: the temp worktree existed at the moment
            # the runner handed it to verify.  Check it from inside
            # this call because the runner deletes the dir after.
            captured["existed_in_fake"] = bool(base_dir) and os.path.isdir(base_dir)
            return VerifyReport(
                head_sha=head_sha, spec_branch=f"spec/{spec_id}",
                status="green", suites=[],
                log_path="/tmp/spec-verify-test.log",
                duration_s=0.1, run_at=time.time(),
            )

        # Don't drive the real ``scripts/verify.sh`` — substitute the
        # ``_execute_verify`` layer, and stub ``_make_temp_spec_worktree``
        # so no real git worktree add is attempted.  The path returned
        # is a freshly-created tempdir so the downstream log write
        # would also resolve.
        base_dir = tempfile.mkdtemp(prefix="spec-verify-test-")
        try:
            with patch("tasks.spec_verify._execute_verify", side_effect=fake_execute), \
                 patch("tasks.spec_verify._merge_task_id_for_sha",
                       return_value=self.task.id), \
                 patch(
                     "tasks.spec_verify._make_temp_spec_worktree",
                     return_value=base_dir,
                 ), \
                 patch(
                     "tasks.spec_verify._project_root",
                     return_value="/srv/operator/checkout",
                 ):
                from tasks.spec_verify import run_spec_verify_blocking
                run_spec_verify_blocking(
                    spec_id="sp_fable_w5", head_sha="abc1234",
                    merge_branch="task/sp_fable_w5/210",
                )
        finally:
            shutil.rmtree(base_dir, ignore_errors=True)

        self.assertEqual(captured["base_dir"], base_dir)
        self.assertTrue(
            captured["existed_in_fake"],
            "base_dir must point at a real tempdir while verify runs",
        )
        # The contract: the runner NEVER runs ``verify.sh`` (or any of
        # its sub-commands) with ``cwd=project_root``.  The base_dir it
        # is given is always a fresh tempdir under /tmp.
        self.assertNotEqual(
            captured["base_dir"], "/srv/operator/checkout",
            "verify must not run inside the operator's main checkout",
        )
        tmp_root = os.path.realpath(tempfile.gettempdir())
        self.assertTrue(
            os.path.realpath(captured["base_dir"] or "").startswith(tmp_root),
            f"base_dir must be under the system tempdir, got {captured['base_dir']!r}",
        )


class MultipleSpecsNoBleedTests(APITestCase):
    """The coordinator's per-spec state must NOT bleed across specs."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.spec_alpha = self.make_spec(
            self.board, odin_id="sp_alpha",
            metadata={"branch": "spec/sp_alpha"},
        )
        self.spec_beta = self.make_spec(
            self.board, odin_id="sp_beta",
            metadata={"branch": "spec/sp_beta"},
        )
        self.task_alpha = self.make_task(
            self.board, spec=self.spec_alpha, status=TaskStatus.REVIEW,
            metadata={"branch": "task/sp_alpha/1"},
        )
        self.task_beta = self.make_task(
            self.board, spec=self.spec_beta, status=TaskStatus.REVIEW,
            metadata={"branch": "task/sp_beta/2"},
        )

    def tearDown(self):
        from tasks import spec_verify
        spec_verify._coordinator._reset_for_tests()
        super().tearDown()

    def test_red_on_alpha_does_not_flag_beta(self):
        """A red run on spec_alpha must not touch spec_beta metadata."""
        from tasks.spec_verify import VerifyReport, run_spec_verify_blocking

        report_a = VerifyReport(
            head_sha="shaA", spec_branch="spec/sp_alpha",
            status="red", suites=[{"name": "backend", "verdict": "FAIL"}],
            log_path="/tmp/A.log", duration_s=1.0, run_at=time.time(),
        )
        base_dir = tempfile.mkdtemp(prefix="spec-verify-alpha-")
        try:
            with patch("tasks.spec_verify._execute_verify", return_value=report_a), \
                 patch("tasks.spec_verify._merge_task_id_for_sha",
                       return_value=self.task_alpha.id), \
                 patch(
                     "tasks.spec_verify._make_temp_spec_worktree",
                     return_value=base_dir,
                 ):
                run_spec_verify_blocking(
                    spec_id="sp_alpha", head_sha="shaA",
                    merge_branch="task/sp_alpha/1",
                )
        finally:
            shutil.rmtree(base_dir, ignore_errors=True)

        self.spec_alpha.refresh_from_db()
        self.spec_beta.refresh_from_db()
        self.assertEqual(self.spec_alpha.metadata.get("verify_status"), "red")
        self.assertNotIn("verify_status", self.spec_beta.metadata or {})
