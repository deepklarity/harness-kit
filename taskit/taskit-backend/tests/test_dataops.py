"""Tests for the data-operations registry (task #247).

Schema migrations have Django; *data* operations shipped by agents had
hope — nothing ran them after merge. The dataops registry fixes that:
each idempotent data operation registers itself, and a ``post_migrate``
hook runs any not-yet-marked op at service start (same lifecycle as
``migrate``), so a restart is all an agent-shipped data op needs.

The done-marker (DataOpMarker) is bookkeeping, not the guard — every op
must be idempotent on its own; the marker just skips redundant work.

Scenario matrix:
  Registry / lifecycle:
   - op runs once, marker created
   - re-run skips the marked op (returns nothing, op not re-executed)
   - a second registered op is unaffected by the first's marker
   - registering a duplicate name raises
  Registration:
   - import_pending_ledger_entries is registered as the first op
  Integration:
   - running dataops creates the ErrorEvent pending rows + marker
   - a second run adds nothing
   - post_migrate signal triggers run_pending_dataops
"""
import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from tests.base import APITestCase
from tasks import dataops
from tasks.models import DataOpMarker, ErrorEvent

_PROBE_NAMES = ("test_probe_a", "test_probe_b", "test_probe_dup")
_probe_count = [0]


def _probe_op():
    """Throwaway op whose call count tests assert against.

    Multiple registry names can point at this one function; the
    return value of ``run_pending_dataops`` distinguishes *which* name
    ran, while this counter proves *how many times* a probe fired.
    """
    _probe_count[0] += 1
    return _probe_count[0]


class DataOpsRegistryTests(APITestCase):

    def setUp(self):
        super().setUp()
        DataOpMarker.objects.all().delete()
        _probe_count[0] = 0

    def tearDown(self):
        for name in _PROBE_NAMES:
            dataops._REGISTRY.pop(name, None)
        super().tearDown()

    def test_op_runs_once_and_marker_set(self):
        dataops.register_dataop("test_probe_a")(_probe_op)
        ran = dataops.run_pending_dataops()
        names = [r["name"] for r in ran]
        self.assertIn("test_probe_a", names)
        self.assertTrue(DataOpMarker.objects.filter(name="test_probe_a").exists())
        self.assertEqual(_probe_count[0], 1)

    def test_rerun_skips_marked_op(self):
        dataops.register_dataop("test_probe_b")(_probe_op)
        dataops.run_pending_dataops()
        self.assertEqual(_probe_count[0], 1)

        ran_again = dataops.run_pending_dataops()
        self.assertNotIn(
            "test_probe_b", [r["name"] for r in ran_again],
            "marked op must not appear in the 'ran' list on a second call",
        )
        self.assertEqual(_probe_count[0], 1)

    def test_second_op_unaffected_by_first_marker(self):
        dataops.register_dataop("test_probe_a")(_probe_op)
        dataops.register_dataop("test_probe_b")(_probe_op)
        DataOpMarker.objects.create(name="test_probe_a")
        ran = dataops.run_pending_dataops()
        names = [r["name"] for r in ran]
        self.assertNotIn("test_probe_a", names)
        self.assertIn("test_probe_b", names)
        self.assertEqual(_probe_count[0], 1)

    def test_duplicate_registration_raises(self):
        dataops.register_dataop("test_probe_dup")(_probe_op)
        with self.assertRaises(ValueError):
            dataops.register_dataop("test_probe_dup")(_probe_op)


class ImportPendingLedgerRegistrationTests(APITestCase):

    def setUp(self):
        super().setUp()
        DataOpMarker.objects.all().delete()
        ErrorEvent.objects.filter(source_id__startswith="pending:").delete()

    def test_import_pending_ledger_is_registered(self):
        self.assertIn("import_pending_ledger_entries", dataops.registered_names())

    def test_running_dataops_imports_entries_and_marks_done(self):
        ran = dataops.run_pending_dataops()
        by_name = {r["name"]: r for r in ran}
        self.assertIn("import_pending_ledger_entries", by_name)
        self.assertEqual(by_name["import_pending_ledger_entries"]["result"], 4)

        self.assertEqual(
            ErrorEvent.objects.filter(source_id__startswith="pending:").count(), 4,
        )
        self.assertTrue(
            DataOpMarker.objects.filter(name="import_pending_ledger_entries").exists(),
        )

    def test_second_run_adds_nothing(self):
        dataops.run_pending_dataops()
        ran_again = dataops.run_pending_dataops()
        self.assertNotIn(
            "import_pending_ledger_entries", [r["name"] for r in ran_again],
        )
        self.assertEqual(
            ErrorEvent.objects.filter(source_id__startswith="pending:").count(), 4,
        )


class PostMigrateHookTests(APITestCase):

    def setUp(self):
        super().setUp()
        DataOpMarker.objects.all().delete()
        ErrorEvent.objects.filter(source_id__startswith="pending:").delete()

    def test_post_migrate_signal_runs_dataops(self):
        from django.apps import apps
        from django.db.models.signals import post_migrate

        app_config = apps.get_app_config("tasks")
        post_migrate.send(sender=app_config, app_config=app_config)

        self.assertTrue(
            DataOpMarker.objects.filter(name="import_pending_ledger_entries").exists(),
            "post_migrate must run the dataop and set the marker",
        )
        self.assertEqual(
            ErrorEvent.objects.filter(source_id__startswith="pending:").count(), 4,
        )
