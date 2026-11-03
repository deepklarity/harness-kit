from celery import shared_task


@shared_task
def process_order(oid):
    return oid
