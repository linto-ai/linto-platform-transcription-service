import os

from celery import Celery

celery = Celery(
    __name__, include=["transcriptionservice.transcription.transcription_task"]
)
service_name = os.environ.get("SERVICE_NAME", "stt")
broker_url = os.environ.get("SERVICES_BROKER", "redis://localhost:6379")
if os.environ.get("BROKER_PASS", False):
    components = broker_url.split("//")
    broker_url = f'{components[0]}//:{os.environ.get("BROKER_PASS")}@{components[1]}'
broker_port = int(os.environ.get("SERVICES_BROKER_PORT", 6379))

celery.conf.broker_url = "{}/0".format(broker_url)
celery.conf.result_backend = "{}/1".format(broker_url)
celery.conf.task_acks_late = False
celery.conf.task_track_started = True
celery.conf.broker_transport_options = {"visibility_timeout": float("inf")}
# celery.conf.result_backend_transport_options = {"visibility_timeout": float("inf")}
# celery.conf.result_expires = 3600 * 24

# Queues
celery.conf.update(
    {
        "task_routes": {
            "transcription_task": {"queue": "{}_requests".format(service_name)},
            # Not Implemented
            # "transcription_task_multi": {"queue": "{}_requests".format(service_name)},
        }
    }
)
