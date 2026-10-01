from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from juya_miniapp_api.infrastructure.security.field_cipher import FieldCipher
from juya_miniapp_api.modules.contacts.repository import InMemoryContactRepository
from juya_miniapp_api.modules.contacts.router import create_contacts_router
from juya_miniapp_api.modules.contacts.service import ContactService


@pytest.mark.asyncio
async def test_contact_prompt_exposure_is_authenticated_and_idempotent() -> None:
    repo = InMemoryContactRepository()
    service = ContactService(repo, FieldCipher(b"k" * 32, b"h" * 32))

    async def user() -> str:
        return "user"

    app = FastAPI()
    app.include_router(
        create_contacts_router(service, user_dependency=user, clock=lambda: datetime.now(UTC))
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        one = await client.post(
            "/api/v1/me/contact/prompt-exposures", headers={"Idempotency-Key": "prompt-1"}
        )
        two = await client.post(
            "/api/v1/me/contact/prompt-exposures", headers={"Idempotency-Key": "prompt-1"}
        )
    assert one.status_code == 200
    assert one.json() == {"created": True}
    assert two.json() == {"created": False}
