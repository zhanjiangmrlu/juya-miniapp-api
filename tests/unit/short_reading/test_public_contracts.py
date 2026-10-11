from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.exceptions import ResponseValidationError
from fastapi.testclient import TestClient

from juya_miniapp_api.integrations.admin_api.schemas import SignedResource
from juya_miniapp_api.modules.short_reading.authority import V15ReadingAuthority
from juya_miniapp_api.modules.short_reading.domain import ReadingDetail, ReadingPreview
from juya_miniapp_api.modules.short_reading.repository import InMemoryReadingRepository
from juya_miniapp_api.modules.short_reading.router import create_short_reading_router
from juya_miniapp_api.modules.short_reading.service import ShortReadingService

NOW = datetime(2026, 10, 11, tzinfo=UTC)


class Authority:
    def __init__(self):
        self.leak = False

    async def listing(self, user_id, cursor, limit):
        row = (await self.preview(user_id, "reading")).model_dump(mode="json")
        if self.leak:
            row["translation_zh"] = "full private translation"
        return {"items": [row], "next_cursor": None, "has_more": False}

    async def preview(self, user_id, content_id):
        return ReadingPreview(
            content_id=content_id,
            title_en="Title",
            title_zh="标题",
            category="生活",
            topic="生活",
            level="初级",
            introduction="安全简介",
            access="TRIAL",
        )

    async def open_reading(self, user_id, content_id):
        return ReadingDetail(
            **(await self.preview(user_id, content_id)).model_dump(),
            content_version=1,
            revision_id="revision",
            paragraphs_en=[{"paragraph_id": "p1", "english": "Hello."}],
            translation_zh="你好。",
            whole_audio=None,
        )

    async def resource(self, user_id, content_id, target, revision):
        return SignedResource(
            resource_id=target,
            url="https://media.example/temporary",
            expires_at=NOW + timedelta(seconds=30),
        )


def application(authority: Authority) -> FastAPI:
    async def owner() -> str:
        return "owner"

    app = FastAPI()
    service = ShortReadingService(authority, InMemoryReadingRepository())
    app.include_router(
        create_short_reading_router(
            service, cast(V15ReadingAuthority, authority), user_dependency=owner, clock=lambda: NOW
        )
    )
    return app


def test_reading_openapi_exposes_each_safe_preview_detail_progress_and_resource_shape() -> None:
    schema = application(Authority()).openapi()
    shapes = schema["components"]["schemas"]
    expected = [
        ("get", "/api/v1/short-readings", "ReadingPageResponse"),
        ("get", "/api/v1/short-readings/{identity}/preview", "ReadingPreview"),
        ("post", "/api/v1/short-readings/{identity}/open", "ReadingDetail"),
        ("put", "/api/v1/short-readings/{identity}/progress", "ReadingProgress"),
        ("post", "/api/v1/short-readings/{identity}/complete", "ReadingCompleteResponse"),
        (
            "get",
            "/api/v1/short-readings/{identity}/resources/{target}/signed-url",
            "ReadingResourceResponse",
        ),
    ]
    for method, path, name in expected:
        response = schema["paths"][path][method]["responses"]["200"]["content"]["application/json"][
            "schema"
        ]
        assert response.get("$ref") == "#/components/schemas/" + name
        assert shapes[name]["additionalProperties"] is False
    preview = shapes["ReadingPreview"]["properties"]
    assert not {"translation_zh", "paragraphs_en", "whole_audio", "progress"}.intersection(preview)
    assert not {"words", "chunks", "explanation", "sense_id"}.intersection(
        shapes["ReadingDetail"]["properties"]
    )
    assert (
        shapes["ReadingCompleteResponse"]["properties"]["progress"]["$ref"]
        == "#/components/schemas/ReadingProgress"
    )


def test_reading_actual_json_remains_flat_and_complete_keeps_progress_wrapper() -> None:
    with TestClient(application(Authority())) as client:
        page = client.get("/api/v1/short-readings").json()
        assert set(page) == {"items", "next_cursor", "has_more"}
        assert "translation_zh" not in page["items"][0]
        opened = client.post("/api/v1/short-readings/reading/open").json()
        assert opened["content_type"] == "short_reading" and opened["whole_audio"] is None
        assert opened["translation_zh"] == "你好。" and "data" not in opened
        saved = client.put(
            "/api/v1/short-readings/reading/progress",
            json={"content_version": 1, "paragraph_id": "p1", "offset": 2, "client_sequence": 1},
        )
        assert saved.json()["offset"] == 2 and saved.json()["client_sequence"] == 1
        completed = client.post(
            "/api/v1/short-readings/reading/complete",
            json={"content_version": 1},
            headers={"Idempotency-Key": "done"},
        )
        assert set(completed.json()) == {"progress", "created"}
        assert completed.json()["created"] is True
        assert "created" not in completed.json()["progress"]
        resource = client.get(
            "/api/v1/short-readings/reading/resources/whole/signed-url",
            params={"revision_id": "revision"},
        )
        assert set(resource.json()) == {"resource_id", "url", "expires_at"}
        assert resource.headers["Cache-Control"] == "no-store"


def test_reading_list_response_rejects_full_translation_in_safe_preview() -> None:
    authority = Authority()
    authority.leak = True
    with TestClient(application(authority)) as client, pytest.raises(ResponseValidationError):
        client.get("/api/v1/short-readings")
