from django.apps import AppConfig


class TasksConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tasks"

    def ready(self):
        import tasks.signals  # noqa: F401
        import tasks.dag_executor  # noqa: F401 — register Celery tasks
