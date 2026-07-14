"""CELERY_TASK_ROUTES config tests for the merge-family task family.

The merge queue exists so a busy default worker pool can't starve merges
behind long-running executions (task #196). Every merge-family celery task
that gets dispatched from web/worker code must be routed to it when
``MERGE_QUEUE_NAME`` is set; otherwise merge work sits behind executions and
incurs minutes of pickup lag (observed live during the reply-resume gap).

The two merge-family tasks in ``tasks.dag_executor``:

- ``merge_task_on_reflection`` — dispatched from the post-reflection merge
  pass and from the merge watchdog scan.
- ``resume_merge_with_guidance`` — dispatched from the human-reply signal
  in ``tasks.signals`` when a ``needs_human`` task gets an answer.

The dispatch style differs (some callers use ``.delay()``, one uses
``.apply_async(queue=...)``) but the route name in ``CELERY_TASK_ROUTES``
covers both: celery resolves the route by task name before sending, so
``.delay()`` on the resumed task lands on the merges queue too.
"""

import importlib
import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from django.conf import settings as django_settings
from django.test import SimpleTestCase, override_settings

from config import settings as config_settings
from tasks.dag_executor import (
    merge_task_on_reflection,
    resume_merge_with_guidance,
)


# The two merge-family task names (matches @shared_task(name=...) in code).
_MERGE_FAMILY = (
    "tasks.dag_executor.merge_task_on_reflection",
    "tasks.dag_executor.resume_merge_with_guidance",
)


class BuildCeleryTaskRoutesTests(SimpleTestCase):
    """The routing builder is the source of truth — all assertions go
    through it. The module-level ``CELERY_TASK_ROUTES`` constant is just
    the builder's output with env values at import time."""

    def test_no_routes_when_neither_queue_set(self):
        from config.settings import _build_celery_task_routes
        self.assertEqual(_build_celery_task_routes(), {})

    def test_merge_queue_only_routes_both_merge_family_tasks(self):
        from config.settings import _build_celery_task_routes
        routes = _build_celery_task_routes(merge_queue="merges")
        self.assertEqual(
            routes.get("tasks.dag_executor.merge_task_on_reflection"),
            {"queue": "merges"},
        )
        self.assertEqual(
            routes.get("tasks.dag_executor.resume_merge_with_guidance"),
            {"queue": "merges"},
        )
        self.assertEqual(len(routes), 2)

    def test_reflection_queue_only_routes_one_task(self):
        from config.settings import _build_celery_task_routes
        routes = _build_celery_task_routes(reflection_queue="reflections")
        self.assertEqual(
            routes.get("tasks.dag_executor.execute_reflection"),
            {"queue": "reflections"},
        )
        for name in _MERGE_FAMILY:
            self.assertNotIn(name, routes)

    def test_both_queues_are_independent(self):
        from config.settings import _build_celery_task_routes
        routes = _build_celery_task_routes(
            merge_queue="merges", reflection_queue="reflections",
        )
        self.assertEqual(routes.get(
            "tasks.dag_executor.merge_task_on_reflection"), {"queue": "merges"})
        self.assertEqual(routes.get(
            "tasks.dag_executor.resume_merge_with_guidance"), {"queue": "merges"})
        self.assertEqual(routes.get(
            "tasks.dag_executor.execute_reflection"), {"queue": "reflections"})

    def test_empty_string_queue_is_treated_as_unset(self):
        """``os.environ.get(..., '').strip() or None`` — an empty string
        env var must produce no routes so operators who accidentally set
        ``MERGE_QUEUE_NAME=`` don't get silent breakage."""
        from config.settings import _build_celery_task_routes
        self.assertEqual(_build_celery_task_routes(merge_queue=""), {})
        self.assertEqual(_build_celery_task_routes(merge_queue="   "), {})


class ModuleLevelCeleryTaskRoutesTests(SimpleTestCase):
    """The module-level dict is the runtime contract — celery reads it at
    app init, so the assertions below run against the live ``settings``
    module. We test cases that don't conflict with whatever the operator
    has set in the current env: empty queues."""

    def setUp(self):
        # Make sure the test runs without inheriting real env values.
        self._orig_merge = os.environ.pop("MERGE_QUEUE_NAME", None)
        self._orig_reflect = os.environ.pop("REFLECTION_QUEUE_NAME", None)
        self._reload_module()

    def tearDown(self):
        if self._orig_merge is not None:
            os.environ["MERGE_QUEUE_NAME"] = self._orig_merge
        if self._orig_reflect is not None:
            os.environ["REFLECTION_QUEUE_NAME"] = self._orig_reflect
        self._reload_module()

    @staticmethod
    def _reload_module():
        importlib.reload(config_settings)
        # Django settings object holds a cached reference; reload it too.
        from django.conf import LazySettings
        django_settings._wrapped = importlib.reload(
            importlib.import_module("config.settings")
        )

    def test_unset_env_yields_empty_routes(self):
        self._reload_module()
        self.assertEqual(django_settings.CELERY_TASK_ROUTES, {})
        for name in _MERGE_FAMILY:
            self.assertNotIn(name, django_settings.CELERY_TASK_ROUTES)

    def test_set_env_yields_merge_routes(self):
        os.environ["MERGE_QUEUE_NAME"] = "merges"
        self._reload_module()
        try:
            self.assertEqual(
                django_settings.CELERY_TASK_ROUTES.get(
                    "tasks.dag_executor.merge_task_on_reflection"),
                {"queue": "merges"},
            )
            self.assertEqual(
                django_settings.CELERY_TASK_ROUTES.get(
                    "tasks.dag_executor.resume_merge_with_guidance"),
                {"queue": "merges"},
            )
        finally:
            os.environ.pop("MERGE_QUEUE_NAME", None)


class OverrideSettingsCompatibilityTests(SimpleTestCase):
    """``override_settings(MERGE_QUEUE_NAME=...)`` does NOT rebuild
    ``CELERY_TASK_ROUTES`` because the dict is built at module import.
    Operators using override_settings in the Django shell should reach for
    the builder function directly; this test pins that contract so a
    future refactor doesn't quietly break it."""

    def test_override_settings_does_not_mutate_celery_task_routes(self):
        from config.settings import _build_celery_task_routes
        baseline = dict(django_settings.CELERY_TASK_ROUTES)
        with override_settings(MERGE_QUEUE_NAME="merges"):
            routes = _build_celery_task_routes(
                merge_queue=django_settings.MERGE_QUEUE_NAME or "merges",
            )
            self.assertEqual(
                routes.get("tasks.dag_executor.resume_merge_with_guidance"),
                {"queue": "merges"},
            )
        # Module-level dict is unchanged — proven behavior (celery read
        # it once at app init).
        self.assertEqual(django_settings.CELERY_TASK_ROUTES, baseline)


class CeleryTaskNamesExistTests(SimpleTestCase):
    """Sanity: the task names declared in code match the names registered
    in ``CELERY_TASK_ROUTES``. A drift between ``@shared_task(name=...)``
    and the route key would silently route to default — exactly the bug
    task #232 is fixing."""

    def test_task_names_match_route_keys(self):
        from config.settings import _build_celery_task_routes
        routes = _build_celery_task_routes(merge_queue="merges")
        self.assertIn(merge_task_on_reflection.name, routes)
        self.assertIn(resume_merge_with_guidance.name, routes)
