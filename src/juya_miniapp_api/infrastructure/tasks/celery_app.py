from celery import Celery  # type: ignore[import-untyped]
from kombu import Queue  # type: ignore[import-untyped]

from juya_miniapp_api.infrastructure.config import Settings
from juya_miniapp_api.infrastructure.tasks.schedules import BEAT_SCHEDULE, TASK_QUEUES


def create_celery_app(settings: Settings | None = None) -> Celery:
    runtime = settings or Settings()
    redis_url = (
        runtime.redis_url.get_secret_value()
        if runtime.redis_url is not None
        else "redis://localhost:6379/0"
    )
    app = Celery("juya_miniapp_api", broker=redis_url, backend=redis_url)
    app.conf.update(
        task_queues=tuple(Queue(name) for name in TASK_QUEUES),
        task_default_queue="miniapp.account",
        task_serializer="json",
        accept_content=("json",),
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        beat_schedule=BEAT_SCHEDULE,
        broker_connection_retry_on_startup=True,
    )
    return app


celery_app = create_celery_app()
