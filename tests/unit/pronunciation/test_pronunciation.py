from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from juya_miniapp_api.integrations.admin_api.v15_content import SceneDetail
from juya_miniapp_api.modules.favorites.snapshots import (
    ConfirmedSnapshot,
    InMemorySnapshotRepository,
    SnapshotReference,
    SnapshotService,
)
from juya_miniapp_api.modules.pronunciation.adapters import (
    CurrentContentAdapter,
    SnapshotReviewAdapter,
)
from juya_miniapp_api.modules.pronunciation.domain import PronunciationRequest
from juya_miniapp_api.modules.pronunciation.router import create_pronunciation_router
from juya_miniapp_api.modules.pronunciation.service import PronunciationService
from juya_miniapp_api.shared.errors import AppError, install_error_handlers

NOW = datetime(2026, 10, 11, tzinfo=UTC)


class Authority:
    def __init__(self) -> None:
        self.denied = False
        self.version = 1
        self.duplicate = False

    async def confirm_snapshot(self, user_id: str, ref: SnapshotReference) -> ConfirmedSnapshot:
        return ConfirmedSnapshot(
            reference=ref,
            revision_id="r1",
            english="medium",
            meaning_zh="中杯",
            source_sentence="A medium coffee.",
            source_title="咖啡店",
            source_locator={},
            pronunciation={"target_id": "word-target", "resource_version": "audio-v1"},
        )

    async def open_scene(self, user_id: str, content_id: str) -> SceneDetail:
        if self.denied:
            raise AppError("CONTENT_ACCESS_DENIED", "无权", 403)
        entries: list[dict[str, Any]] = [
            {
                "entry_id": "entry",
                "entry_type": "word",
                "sense_id": "sense",
                "source_id": "source",
                "english": "medium",
                "meaning_zh": "中杯",
                "explanation": "",
                "source_sentence": "A medium coffee.",
                "pronunciation": {"target_id": "word-target", "resource_version": "audio-v1"},
            }
        ]
        if self.duplicate:
            entries.append({**entries[0], "source_id": "other-source"})
        return SceneDetail(
            content_id=content_id,
            content_type="dialogue",
            content_version=self.version,
            revision_id="r1",
            title_en="Coffee",
            title_zh="咖啡",
            words=entries,
        )


class InternalClient:
    def __init__(self) -> None:
        self.result: dict[str, Any] = {
            "status": "READY",
            "url": "https://media.example/audio?signature=temporary",
            "resource_version": "audio-v1",
            "expires_at": int((NOW + timedelta(seconds=30)).timestamp()),
        }
        self.contexts: list[dict[str, object]] = []
        self.calls = 0

    async def resolve(self, target_id: str, context: dict[str, object]) -> dict[str, Any]:
        assert target_id == "word-target"
        self.calls += 1
        self.contexts.append(context)
        return self.result

    async def poll(self, task_id: str, context: dict[str, object]) -> dict[str, Any]:
        self.calls += 1
        self.contexts.append(context)
        if task_id == "other-target-task":
            raise AppError("PRONUNCIATION_FORBIDDEN", "任务目标不符", 403)
        return {**self.result, "task_id": task_id}


async def setup_service() -> tuple[
    PronunciationService, SnapshotService, Authority, InternalClient, str
]:
    authority = Authority()
    snapshots = SnapshotService(InMemorySnapshotRepository(), authority)
    row = await snapshots.create(
        "owner",
        SnapshotReference(
            content_type="dialogue",
            content_id="coffee",
            content_version=1,
            entry_type="word",
            entry_id="entry",
            sense_id="sense",
            source_id="source",
        ),
        "create",
        NOW,
    )
    client = InternalClient()
    service = PronunciationService(
        SnapshotReviewAdapter(snapshots),
        CurrentContentAdapter(authority),
        client,
        clock=lambda: NOW,
    )
    return service, snapshots, authority, client, row.favorite_id


def content_request(**updates: Any) -> PronunciationRequest:
    return PronunciationRequest(
        **{
            "content_type": "dialogue",
            "content_id": "coffee",
            "content_version": 1,
            "entry_id": "entry",
            "sense_id": "sense",
            "source_id": "source",
            **updates,
        }
    )


@pytest.mark.asyncio
async def test_expired_source_entitlement_still_allows_fixed_favorite_pronunciation() -> None:
    service, _, authority, client, fid = await setup_service()
    authority.denied = True
    result = await service.resolve("owner", "word-target", PronunciationRequest(favorite_id=fid))
    assert result.status == "READY"
    assert result.resource_version == "audio-v1"
    assert result.expires_at == NOW + timedelta(seconds=30)
    context = client.contexts[0]
    assert context["purpose"] == "FAVORITE_REVIEW"
    assert context["user_id"] == "owner"
    assert context["content_version"] == "1"
    assert context["snapshot_version"] == "1"
    assert "sense_id" not in context and "source_id" not in context
    assert context["nonce"]


@pytest.mark.asyncio
@pytest.mark.parametrize("owner,target", [("stranger", "word-target"), ("owner", "whole-target")])
async def test_foreign_favorite_and_wrong_target_never_reach_internal_client(
    owner: str, target: str
) -> None:
    service, _, _, client, fid = await setup_service()
    with pytest.raises(AppError):
        await service.resolve(owner, target, PronunciationRequest(favorite_id=fid))
    assert client.calls == 0


@pytest.mark.asyncio
async def test_poll_derives_target_from_owned_snapshot_and_rechecks_cancel() -> None:
    service, snapshots, _, client, fid = await setup_service()
    request = PronunciationRequest(favorite_id=fid)
    result = await service.poll("owner", "task", request)
    assert result.status == "READY"
    assert client.contexts[0]["target_id"] == "word-target"
    await snapshots.cancel("owner", fid, "cancel", NOW)
    with pytest.raises(AppError):
        await service.poll("owner", "task", request)
    assert client.calls == 1


@pytest.mark.asyncio
async def test_task_id_does_not_authorize_cross_user_or_unrelated_target() -> None:
    service, _, _, client, fid = await setup_service()
    with pytest.raises(AppError):
        await service.poll("stranger", "task", PronunciationRequest(favorite_id=fid))
    assert client.calls == 0
    with pytest.raises(AppError):
        await service.poll("owner", "other-target-task", PronunciationRequest(favorite_id=fid))


@pytest.mark.asyncio
async def test_current_content_requires_latest_version_exact_entry_and_current_access() -> None:
    service, _, authority, client, _ = await setup_service()
    assert (await service.resolve("owner", "word-target", content_request())).status == "READY"
    assert client.contexts[0]["purpose"] == "CONTENT"
    authority.version = 2
    with pytest.raises(AppError) as error:
        await service.resolve("owner", "word-target", content_request())
    assert error.value.code == "CONTENT_VERSION_CONFLICT"
    authority.version = 1
    for updates in ({"sense_id": "wrong"}, {"source_id": "wrong"}):
        with pytest.raises(AppError):
            await service.resolve("owner", "word-target", content_request(**updates))
    authority.denied = True
    with pytest.raises(AppError):
        await service.poll("owner", "task", content_request())
    assert client.calls == 1


@pytest.mark.asyncio
async def test_ambiguous_entry_is_not_guessed_when_poll_has_no_target() -> None:
    service, _, authority, client, _ = await setup_service()
    authority.duplicate = True
    request = content_request(source_id=None)
    with pytest.raises(AppError):
        await service.poll("owner", "task", request)
    assert client.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "upstream,expected,retry",
    [
        ("READY", "READY", False),
        ("PENDING", "PREPARING", False),
        ("PREPARING", "PREPARING", False),
        ("RETRY", "PREPARING", False),
        ("RUNNING", "PREPARING", False),
        ("RECOVER", "PREPARING", False),
        ("FAILED", "FAILED", True),
        ("UNKNOWN", "FAILED", False),
        ("UNAVAILABLE", "UNAVAILABLE", False),
    ],
)
async def test_internal_states_map_to_four_public_states(
    upstream: str, expected: str, retry: bool
) -> None:
    service, _, _, client, fid = await setup_service()
    client.result = {**client.result, "status": upstream, "task_id": "task", "can_retry": True}
    result = await service.resolve("owner", "word-target", PronunciationRequest(favorite_id=fid))
    assert result.status == expected
    assert result.can_retry is retry
    polled = await service.poll("owner", "task", PronunciationRequest(favorite_id=fid))
    assert polled.status == expected and polled.can_retry is retry
    if expected != "READY":
        assert result.url is None and result.expires_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "updates",
    [
        {"expires_at": int(NOW.timestamp())},
        {"expires_at": None},
        {"url": "javascript:alert(1)"},
        {"resource_version": "audio-v2"},
    ],
)
async def test_expired_missing_or_wrong_ready_resource_is_not_exposed(
    updates: dict[str, Any],
) -> None:
    service, _, _, client, fid = await setup_service()
    client.result = {**client.result, **updates}
    with pytest.raises(AppError) as error:
        await service.resolve("owner", "word-target", PronunciationRequest(favorite_id=fid))
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_resource_disabled_error_becomes_unavailable_without_leaking_internal_details() -> (
    None
):
    service, _, _, client, fid = await setup_service()
    client.result = {
        "status": "UNAVAILABLE",
        "error_code": "PRONUNCIATION_UNAVAILABLE",
        "secret": "hidden",
    }
    result = await service.resolve("owner", "word-target", PronunciationRequest(favorite_id=fid))
    assert result.status == "UNAVAILABLE"
    assert result.code == "PRONUNCIATION_UNAVAILABLE"
    assert "secret" not in result.model_dump()


@pytest.mark.asyncio
async def test_public_routes_forbid_untrusted_authorization_text_and_task_only_query() -> None:
    service, _, _, _, fid = await setup_service()
    app = FastAPI()
    install_error_handlers(app)

    async def user() -> str:
        return "owner"

    app.include_router(create_pronunciation_router(service, user_dependency=user))
    with TestClient(app) as http:
        for field, value in (
            ("authorized", True),
            ("user_id", "stranger"),
            ("text", "arbitrary"),
            ("purpose", "CONTENT"),
        ):
            response = http.post(
                "/api/v1/pronunciations/word-target/resolve",
                json={"favorite_id": fid, field: value},
            )
            assert response.status_code == 422
        assert http.get("/api/v1/pronunciations/tasks/task").status_code == 422
        assert (
            http.get(
                "/api/v1/pronunciations/tasks/task",
                params={"favorite_id": fid, "authorized": "true"},
            ).status_code
            == 422
        )
        response = http.get("/api/v1/pronunciations/tasks/task", params={"favorite_id": fid})
        assert response.status_code == 200
        assert response.json()["status"] == "READY"
        assert response.headers["Cache-Control"] == "private, no-store"


@pytest.mark.asyncio
async def test_malformed_or_mismatched_task_response_is_rejected() -> None:
    service, _, _, client, fid = await setup_service()
    client.result = {"status": "PREPARING", "task_id": "different-task"}

    # Poll must reject a task identifier inconsistent with the requested task.
    class WrongTaskClient:
        async def resolve(self, target_id: str, context: dict[str, object]) -> dict[str, Any]:
            return client.result

        async def poll(self, task_id: str, context: dict[str, object]) -> dict[str, Any]:
            return client.result

    service.client = WrongTaskClient()
    with pytest.raises(AppError) as error:
        await service.poll("owner", "task", PronunciationRequest(favorite_id=fid))
    assert error.value.code == "PRONUNCIATION_CONTRACT_INVALID"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    [
        "PRONUNCIATION_UNAVAILABLE",
        "TTS_DISABLED",
        "TTS_TEXT_LIMIT",
        "PRONUNCIATION_BUDGET_EXCEEDED",
    ],
)
async def test_unavailable_business_errors_preserve_explicit_safe_code(code: str) -> None:
    service, _, _, _, fid = await setup_service()

    class UnavailableClient:
        async def resolve(self, target_id: str, context: dict[str, object]) -> dict[str, Any]:
            raise AppError(code, "internal detail should not leak", 409)

        async def poll(self, task_id: str, context: dict[str, object]) -> dict[str, Any]:
            raise AppError(code, "internal detail should not leak", 409)

    service.client = UnavailableClient()
    result = await service.resolve("owner", "word-target", PronunciationRequest(favorite_id=fid))
    assert result.status == "UNAVAILABLE" and result.code == code
    assert result.url is None and not result.can_retry
    assert (
        await service.poll("owner", "task", PronunciationRequest(favorite_id=fid))
    ).status == "UNAVAILABLE"


@pytest.mark.asyncio
async def test_denied_account_dependency_prevents_favorite_delegation() -> None:
    service, _, _, client, fid = await setup_service()
    app = FastAPI()
    install_error_handlers(app)

    async def denied_account() -> str:
        raise AppError("ACCOUNT_UNAVAILABLE", "账号不可用", 403)

    app.include_router(create_pronunciation_router(service, user_dependency=denied_account))
    with TestClient(app) as http:
        assert (
            http.post(
                "/api/v1/pronunciations/word-target/resolve", json={"favorite_id": fid}
            ).status_code
            == 403
        )
    assert client.calls == 0


@pytest.mark.asyncio
async def test_short_reading_cannot_create_word_pronunciation_via_current_content() -> None:
    service, _, _, client, _ = await setup_service()
    with pytest.raises(AppError):
        await service.resolve("owner", "word-target", content_request(content_type="short_reading"))
    assert client.calls == 0


@pytest.mark.asyncio
async def test_favorite_context_cannot_override_fixed_resource_version() -> None:
    service, _, _, client, fid = await setup_service()
    with pytest.raises(AppError) as error:
        await service.resolve(
            "owner",
            "word-target",
            PronunciationRequest(favorite_id=fid, resource_version="audio-v2"),
        )
    assert error.value.code == "CONTENT_VERSION_CONFLICT"
    assert client.calls == 0


def test_boolean_is_not_a_content_version() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        content_request(content_version=True)
