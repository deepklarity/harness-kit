"""Celery application configuration for TaskIt."""

import os

from celery import Celery
from celery.signals import worker_ready

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("taskit")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@worker_ready.connect
def _reconcile_task_runs_on_boot(**kwargs):
    """W5.14 (task #211) startup sweep: reconcile RUNNING TaskRun rows the
    moment a worker comes up, instead of waiting for the first periodic Beat
    pass. A worker that crashed mid-run must never leave an orphaned run
    (and its sandbox) unsupervised for up to TASK_RUN_RECONCILE_INTERVAL_SECONDS
    after a restart — this runs synchronously, before the worker starts
    consuming tasks, so recovery happens before any fresh dispatch could race it.
    """
    from tasks.dag_executor import reconcile_task_runs
    reconcile_task_runs()
