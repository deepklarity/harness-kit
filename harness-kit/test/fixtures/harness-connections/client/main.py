import requests
from celery import Celery

app = Celery("client")


def fetch_order(oid):
    return requests.get(f"http://service:5000/orders/{oid}").json()


def submit(oid):
    app.send_task("process_order", args=[oid])
