"""TaskIt Django project configuration."""

try:
    from .celery import app as celery_app

    __all__ = ["celery_app"]
except ImportError:
    # Celery is optional — tests and dev can run without it
    pass
