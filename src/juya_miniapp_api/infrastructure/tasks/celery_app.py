import asyncio
from datetime import UTC, datetime

import httpx
from celery import Celery  # type: ignore[import-untyped]
from kombu import Queue  # type: ignore[import-untyped]
from pydantic import SecretStr

from juya_miniapp_api.infrastructure.config import Settings
from juya_miniapp_api.infrastructure.db.session import create_engine, create_session_factory
from juya_miniapp_api.infrastructure.tasks.outbox import OutboxDispatcher, SQLAlchemyOutboxStore
from juya_miniapp_api.infrastructure.tasks.schedules import BEAT_SCHEDULE, TASK_QUEUES
from juya_miniapp_api.integrations.admin_api.client import AdminApiClient
from juya_miniapp_api.modules.accounts.repository import SQLAlchemyAccountRepository
from juya_miniapp_api.modules.accounts.service import AccountLifecycleService
from juya_miniapp_api.modules.accounts.tasks import create_account_cleanup_handler
from juya_miniapp_api.modules.auth.repository import SQLAlchemyAuthRepository


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


def _required_url(value: SecretStr | None, name: str) -> str:
    if value is None:
        raise RuntimeError(f"Missing required setting: {name}")
    return value.get_secret_value()


@celery_app.task(name="juya.accounts.execute_due")  # type: ignore[untyped-decorator]
def execute_due_account_deletions() -> int:
    async def execute() -> int:
        settings = Settings()
        engine = create_engine(_required_url(settings.database_url, "database_url"))
        sessions = create_session_factory(engine)
        auth = SQLAlchemyAuthRepository(sessions)
        service = AccountLifecycleService(SQLAlchemyAccountRepository(sessions), auth.revoke_all)
        try:
            return len(await service.execute_due_deletions(datetime.now(UTC)))
        finally:
            await engine.dispose()

    return asyncio.run(execute())


@celery_app.task(name="juya.accounts.dispatch_outbox")  # type: ignore[untyped-decorator]
def dispatch_account_outbox() -> int:
    async def dispatch() -> int:
        settings = Settings()
        engine = create_engine(_required_url(settings.database_url, "database_url"))
        sessions = create_session_factory(engine)
        http = httpx.AsyncClient(base_url=settings.admin_api_base_url)
        secret = _required_url(settings.internal_hmac_secret, "internal_hmac_secret")
        client = AdminApiClient(http, secret=secret.encode())
        dispatcher = OutboxDispatcher(
            SQLAlchemyOutboxStore(sessions), create_account_cleanup_handler(client)
        )
        try:
            return await dispatcher.dispatch_due(datetime.now(UTC))
        finally:
            await http.aclose()
            await engine.dispose()

    return asyncio.run(dispatch())
