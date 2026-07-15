from django.apps import AppConfig


class TasksConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tasks"

    def ready(self):
        from tasks.forced_provider import validate_forced_provider_config

        validate_forced_provider_config()
        # Apply WAL + busy_timeout to every SQLite connection (task #253).
        # Same hook for runserver, celery workers, and tests — no second
        # settings path to forget.
        from tasks.sqlite_pragmas import connect as connect_sqlite_pragmas

        connect_sqlite_pragmas()
        import tasks.signals  # noqa: F401
        import tasks.notification_signals  # noqa: F401
        import tasks.dag_executor  # noqa: F401 — register Celery tasks
        import tasks.schedule_executor  # noqa: F401 — register schedule release task
        import tasks.failed_reminder  # noqa: F401 — register failed reminder task
        import tasks.board_planner  # noqa: F401 — register board-driven plan tasks

        # Data-operations registry (task #247): run pending data ops after
        # migrate, so a service restart applies shipped data ops with no
        # manual runbook. Same lifecycle as schema migrations.
        from django.db.models.signals import post_migrate
        from tasks.dataops import run_pending_dataops
        post_migrate.connect(run_pending_dataops, sender=self)
