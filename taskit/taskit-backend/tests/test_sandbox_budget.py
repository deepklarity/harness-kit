"""Tests for the global sandbox memory budget (task 203).

The budget coordinates microVM spawns across execution + reflection so the
host never over-commits RAM into swap. Covers:

  1. Accounting — reserved memory is derived from DB state (spawn/release).
  2. Waiting — a spawn that would exceed the budget queues until one releases.
  3. The execution concurrency cap is still respected alongside the new gate.
  4. Reflection self-defers when the budget is full and proceeds when it frees.
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from unittest.mock import patch, MagicMock

from django.test import override_settings

from tests.base import APITestCase
from tasks.models import ReflectionReport, ReflectionStatus, Task, TaskStatus
from tasks.dag_executor import poll_and_execute, execute_reflection
from tasks import sandbox_budget

VM = sandbox_budget.DEFAULT_VM_MEM_MIB  # 4096 — one microVM's provisioned cap


class BudgetAccountingTests(APITestCase):
    """Reserved memory is derived from live DB state (crash-safe)."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def test_reserved_is_zero_with_no_spawns(self):
        self.assertEqual(sandbox_budget.compute_reserved_mib(), 0)

    def test_reserved_counts_executing_task_with_stamp(self):
        self.make_task(
            self.board, status=TaskStatus.EXECUTING,
            metadata={"active_execution": {"mem_mib": 2048}},
        )
        self.assertEqual(sandbox_budget.compute_reserved_mib(), 2048)

    def test_reserved_defaults_unstamped_executing_to_vm_default(self):
        """A live EXECUTING VM consumes memory whether or not the stamp is
        present — account the default so the budget never under-counts."""
        self.make_task(self.board, status=TaskStatus.EXECUTING, metadata={})
        self.assertEqual(sandbox_budget.compute_reserved_mib(), VM)

    def test_reserved_counts_running_reflection(self):
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        ReflectionReport.objects.create(
            task=task, reviewer_agent="claude", reviewer_model="m",
            status=ReflectionStatus.RUNNING,
        )
        self.assertEqual(sandbox_budget.compute_reserved_mib(), VM)

    def test_reserved_ignores_non_executing_tasks(self):
        self.make_task(
            self.board, status=TaskStatus.IN_PROGRESS,
            metadata={"active_execution": {"mem_mib": 2048}},
        )
        self.assertEqual(sandbox_budget.compute_reserved_mib(), 0)

    def test_reserved_ignores_non_running_reflections(self):
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        ReflectionReport.objects.create(
            task=task, reviewer_agent="claude", reviewer_model="m",
            status=ReflectionStatus.PENDING,
        )
        self.assertEqual(sandbox_budget.compute_reserved_mib(), 0)

    def test_reserved_sums_mixed_spawns(self):
        self.make_task(
            self.board, status=TaskStatus.EXECUTING,
            metadata={"active_execution": {"mem_mib": 2048}},
        )
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        ReflectionReport.objects.create(
            task=task, reviewer_agent="claude", reviewer_model="m",
            status=ReflectionStatus.RUNNING,
        )
        # 2048 (executing) + VM (running reflection)
        self.assertEqual(sandbox_budget.compute_reserved_mib(), 2048 + VM)


class SpawnFitsTests(APITestCase):
    """spawn_fits is the gate primitive the dispatch sites call."""

    def test_fits_under_budget(self):
        self.assertTrue(sandbox_budget.spawn_fits(VM, reserved=VM, budget_mib=VM * 2))

    def test_does_not_fit_over_budget(self):
        # budget == VM, VM already reserved → another VM does not fit
        self.assertFalse(sandbox_budget.spawn_fits(VM, reserved=VM, budget_mib=VM))

    def test_fits_exactly_at_budget(self):
        # budget == VM, nothing reserved → exactly one VM fits (<=)
        self.assertTrue(sandbox_budget.spawn_fits(VM, reserved=0, budget_mib=VM))

    def test_unbounded_budget_always_fits(self):
        # budget_mib=None means "no budget configured" → never gate
        self.assertTrue(sandbox_budget.spawn_fits(999999, reserved=0, budget_mib=None))


class BudgetResolutionTests(APITestCase):
    """get_budget_mib resolves explicit setting vs host-derived default."""

    def test_explicit_setting_wins(self):
        with override_settings(SANDBOX_MEMORY_BUDGET_MIB=9999):
            self.assertEqual(sandbox_budget.get_budget_mib(), 9999)

    def test_zero_setting_computes_from_host(self):
        with override_settings(SANDBOX_MEMORY_BUDGET_MIB=0):
            with patch.object(sandbox_budget, "_host_ram_mib", return_value=20000):
                # 20000 MiB host − 6144 reserve
                self.assertEqual(sandbox_budget.get_budget_mib(), 20000 - 6144)

    def test_zero_setting_unbounded_when_host_unknown(self):
        """On a host where RAM can't be detected, default to unbounded so
        dev/test boxes without /proc aren't choked (opt-in via env instead)."""
        with override_settings(SANDBOX_MEMORY_BUDGET_MIB=0):
            with patch.object(sandbox_budget, "_host_ram_mib", return_value=None):
                self.assertIsNone(sandbox_budget.get_budget_mib())

    def test_zero_setting_unbounded_when_host_too_small(self):
        """A host that can't fit even one VM after the OS reserve gets an
        unbounded budget — gating would otherwise deadlock (nothing fits),
        so the concurrency cap takes over on small / CI hosts."""
        with override_settings(SANDBOX_MEMORY_BUDGET_MIB=0):
            with patch.object(sandbox_budget, "_host_ram_mib", return_value=8000):
                # 8000 − 6144 = 1856 < 4096 (one VM) → unbounded
                self.assertIsNone(sandbox_budget.get_budget_mib())


class PollMemoryGateTests(APITestCase):
    """poll_and_execute holds ready tasks when the memory budget is full."""

    def setUp(self):
        super().setUp()
        # allow_project_root_execution so spec-less tasks dispatch (matches
        # the existing PollAndExecuteTests convention).
        self.board = self.make_board(allow_project_root_execution=True)
        self.user = self.make_user()

    @patch("tasks.dag_executor.execute_single_task")
    @override_settings(SANDBOX_MEMORY_BUDGET_MIB=VM * 2, DAG_EXECUTOR_MAX_CONCURRENCY=10)
    def test_third_spawn_queues_until_one_releases(self, mock_exec):
        """Two live VMs fill a 2-VM budget; a third ready task is held until a
        live VM releases. This is the core acceptance criterion."""
        mock_exec.delay.return_value.id = "fake"

        # Two VMs already live → budget full
        for i in range(2):
            self.make_task(
                self.board, title=f"Live {i}", status=TaskStatus.EXECUTING,
                metadata={"active_execution": {"mem_mib": VM}},
            )
        ready = [
            self.make_task(
                self.board, title=f"Ready {i}", status=TaskStatus.IN_PROGRESS,
                assignee=self.user,
            )
            for i in range(3)
        ]

        poll_and_execute()

        # Budget full → none dispatched, each stamped memory_budget_full
        mock_exec.delay.assert_not_called()
        for t in ready:
            t.refresh_from_db()
            self.assertEqual(t.metadata.get("dispatch_blocked_reason"), "memory_budget_full")

        # Release one VM (EXECUTING → REVIEW)
        live = Task.objects.filter(status=TaskStatus.EXECUTING).first()
        live.status = TaskStatus.REVIEW
        live.save(update_fields=["status"])

        mock_exec.delay.reset_mock()
        poll_and_execute()

        # Exactly one slot freed → exactly one dispatch
        self.assertEqual(mock_exec.delay.call_count, 1)

    @patch("tasks.dag_executor.execute_single_task")
    @override_settings(SANDBOX_MEMORY_BUDGET_MIB=VM * 3, DAG_EXECUTOR_MAX_CONCURRENCY=2)
    def test_concurrency_cap_still_respected_with_roomy_budget(self, mock_exec):
        """With a loose memory budget, the count cap still binds — proving the
        two gates coexist (acceptance: cap still respected)."""
        mock_exec.delay.return_value.id = "fake"
        self.make_task(
            self.board, status=TaskStatus.EXECUTING,
            metadata={"active_execution": {"mem_mib": VM}},
        )
        for i in range(3):
            self.make_task(
                self.board, title=f"Ready {i}", status=TaskStatus.IN_PROGRESS,
                assignee=self.user,
            )
        poll_and_execute()
        # cap=2, 1 executing → 1 slot regardless of the roomy memory budget
        self.assertEqual(mock_exec.delay.call_count, 1)

    @patch("tasks.dag_executor.execute_single_task")
    @override_settings(SANDBOX_MEMORY_BUDGET_MIB=VM * 2)
    def test_dispatched_task_stamps_mem_mib(self, mock_exec):
        """A dispatched task records its reservation so future polls account it."""
        mock_exec.delay.return_value.id = "fake"
        t = self.make_task(
            self.board, status=TaskStatus.IN_PROGRESS, assignee=self.user,
        )
        poll_and_execute()
        self.assertEqual(mock_exec.delay.call_count, 1)
        t.refresh_from_db()
        self.assertEqual(t.metadata["active_execution"]["mem_mib"], VM)

    @patch("tasks.dag_executor.execute_single_task")
    @override_settings(SANDBOX_MEMORY_BUDGET_MIB=VM, DAG_EXECUTOR_MAX_CONCURRENCY=10)
    def test_unbounded_when_budget_unset_and_dispatch_proceeds(self, mock_exec):
        """With budget unset (None) the gate never binds — legacy behavior."""
        mock_exec.delay.return_value.id = "fake"
        with override_settings(SANDBOX_MEMORY_BUDGET_MIB=0):
            with patch.object(sandbox_budget, "_host_ram_mib", return_value=None):
                self.make_task(
                    self.board, status=TaskStatus.IN_PROGRESS, assignee=self.user,
                )
                poll_and_execute()
                self.assertEqual(mock_exec.delay.call_count, 1)


class ReflectionMemoryGateTests(APITestCase):
    """execute_reflection self-defers while the budget is full."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board(allow_project_root_execution=True)
        self.task = self.make_task(self.board, status=TaskStatus.REVIEW)

    def _make_report(self):
        return ReflectionReport.objects.create(
            task=self.task, reviewer_agent="claude", reviewer_model="m",
            status=ReflectionStatus.PENDING,
        )

    @patch.object(execute_reflection, "apply_async", create=True)
    @patch("tasks.dag_executor.subprocess.run")
    @override_settings(SANDBOX_MEMORY_BUDGET_MIB=VM)
    def test_reflection_redelays_when_budget_full(self, mock_run, mock_apply):
        """Budget full (one executing VM) → reflection re-delays itself, stays
        PENDING, never launches the subprocess."""
        # Fill the budget with one executing VM
        self.make_task(
            self.board, status=TaskStatus.EXECUTING,
            metadata={"active_execution": {"mem_mib": VM}},
        )
        report = self._make_report()

        execute_reflection(report.id)

        mock_run.assert_not_called()
        mock_apply.assert_called_once()
        # countdown must be passed (waiting in line, not tight loop)
        _, kwargs = mock_apply.call_args
        self.assertIn("countdown", kwargs)
        self.assertGreater(kwargs["countdown"], 0)
        report.refresh_from_db()
        self.assertEqual(report.status, ReflectionStatus.PENDING)

    @patch.object(execute_reflection, "apply_async", create=True)
    @patch("tasks.dag_executor.subprocess.run")
    @override_settings(SANDBOX_MEMORY_BUDGET_MIB=VM * 2)
    def test_reflection_runs_when_budget_fits(self, mock_run, mock_apply):
        """Budget has room → reflection launches the subprocess (no re-delay)."""
        mock_run.return_value = MagicMock(returncode=0)
        report = self._make_report()

        execute_reflection(report.id)

        mock_run.assert_called_once()
        mock_apply.assert_not_called()
