try:
    from celery import shared_task
except ImportError:
    def shared_task(*args, **kwargs):
        def decorator(func):
            func.delay = lambda *a, **kw: func(*a, **kw)
            return func
        if args and callable(args[0]):
            return decorator(args[0])
        return decorator

from .scheduling import release_due_schedules


@shared_task(name="tasks.schedule_executor.release_due_schedules")
def release_due_schedules_task():
    return release_due_schedules()
