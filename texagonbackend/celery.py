import os
from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "texagonbackend.settings")

app = Celery("texagonbackend")
app.conf.broker_url = os.environ.get("REDIS_URL")
app.conf.result_backend = os.environ.get("REDIS_URL")

# Route tasks -> queues
app.conf.task_routes = {
    "notifications.tasks.*": {"queue": "email"},
    "billing.tasks.*": {"queue": "billing"},
}

app.autodiscover_tasks()
