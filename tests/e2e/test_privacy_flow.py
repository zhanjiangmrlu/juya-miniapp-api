from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from juya_miniapp_api.modules.accounts.repository import InMemoryAccountRepository
from juya_miniapp_api.modules.accounts.router import create_accounts_router
from juya_miniapp_api.modules.accounts.service import AccountLifecycleService
from juya_miniapp_api.shared.errors import install_error_handlers


@pytest.mark.asyncio
async def test_clear_then_request_and_revoke_deletion_over_http() -> None:
    repository = InMemoryAccountRepository()
    repository.seed_user("user-1")
    repository.learning_rows.add("user-1")

    async def current_user() -> str:
        return "user-1"

    async def revoke_sessions(user_id: str, reason: str, now: datetime) -> None:
        del user_id, reason, now

    now = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)
    service = AccountLifecycleService(repository, revoke_sessions)
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(
        create_accounts_router(service, user_dependency=current_user, clock=lambda: now)
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        cleared = await client.request(
            "DELETE",
            "/api/v1/me/learning-data",
            json={"confirmation": "CLEAR_LEARNING_DATA"},
        )
        requested = await client.post("/api/v1/me/deletion")
        revoked = await client.post("/api/v1/me/deletion/revoke")

    assert cleared.status_code == 204
    assert "user-1" not in repository.learning_rows
    assert requested.status_code == 202
    assert requested.json()["status"] == "PENDING"
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "REVOKED"
