from django.apps import AppConfig


class TasksConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tasks"

    def ready(self):
        from tasks.forced_provider import validate_forced_provider_config

        validate_forced_provider_config()
        import tasks.signals  # noqa: F401
        import tasks.notification_signals  # noqa: F401
        import tasks.dag_executor  # noqa: F401 — register Celery tasks
        import tasks.schedule_executor  # noqa: F401 — register schedule release task
