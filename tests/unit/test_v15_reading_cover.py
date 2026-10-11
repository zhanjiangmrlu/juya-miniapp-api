from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest

from juya_miniapp_api.modules.short_reading.authority import V15ReadingAuthority
from juya_miniapp_api.shared.errors import AppError


def preview(cover="cover", version=1):
    return {
        "content_id": "reading",
        "content_type": "short_reading",
        "content_version": version,
        "title_en": "Safe",
        "title_zh": "安全",
        "category": "daily",
        "topic": "coffee",
        "level": "A1",
        "safe_intro": "Safe introduction only",
        "cover_asset_id": cover,
    }


def signed():
    return {
        "target_id": "cover",
        "asset_id": "cover",
        "role": "COVER",
        "resource_version": None,
        "duration_ms": None,
        "url": "https://media.invalid/cover?signature=private-fixture",
        "expires_at": (datetime.now(UTC) + timedelta(seconds=40)).isoformat(),
    }


def authority(*, source=None, resource=None):
    client = AsyncMock()
    client.preview.return_value = source or preview()
    client.preview_cover.return_value = resource or signed()
    result = V15ReadingAuthority(None, client)
    result.access = AsyncMock(return_value="PREVIEW")
    return result, client


@pytest.mark.asyncio
async def test_safe_preview_exposes_only_current_signed_cover_without_full_access():
    value, client = authority()
    result = await value.preview("owner", "reading")
    assert result.cover_url == client.preview_cover.return_value["url"]
    assert result.access == "PREVIEW"
    assert not {"paragraphs_en", "whole_audio", "cover_asset_id", "content_version"}.intersection(
        result.model_dump()
    )
    assert client.preview.await_count == 2
    client.preview_cover.assert_awaited_once_with("owner", "short_reading", "reading")
    value.access.assert_awaited_once_with("owner", "reading")


@pytest.mark.asyncio
async def test_missing_cover_returns_null_and_does_not_request_a_signature():
    value, client = authority(source=preview(None))
    assert (await value.preview("owner", "reading")).cover_url is None
    client.preview_cover.assert_not_awaited()


@pytest.mark.parametrize(
    "patch",
    [
        {"target_id": "foreign"},
        {"asset_id": "foreign"},
        {"role": "WHOLE"},
        {"resource_version": "audio-v1"},
        {"duration_ms": 100},
        {"url": "http://media.invalid/cover"},
        {"url": "https://user:secret@media.invalid/cover"},
        {"url": "https://media.invalid/cover#fragment"},
        {"url": "https:///cover"},
        {"expires_at": "2020-01-01T00:00:00+00:00"},
        {"expires_at": "2099-01-01T00:00:00Z"},
        {"expires_at": "2099-01-01T00:00:00"},
        {"expires_at": True},
        {"expires_at": datetime.now(UTC).timestamp() + 40},
        {"expires_at": str(datetime.now(UTC).timestamp() + 40)},
        {"unexpected_body": "must not leak"},
    ],
)
@pytest.mark.asyncio
async def test_invalid_upstream_cover_fails_closed(patch):
    resource = signed() | patch
    value, _ = authority(resource=resource)
    with pytest.raises(AppError) as caught:
        await value.preview("owner", "reading")
    assert caught.value.status_code == 503


@pytest.mark.parametrize(
    "field", ["target_id", "asset_id", "resource_version", "duration_ms", "role"]
)
@pytest.mark.asyncio
async def test_cover_contract_requires_every_binding_field(field):
    resource = signed()
    del resource[field]
    value, _ = authority(resource=resource)
    with pytest.raises(AppError):
        await value.preview("owner", "reading")


@pytest.mark.parametrize("changed", [preview("new-cover"), preview("cover", 2), preview(None)])
@pytest.mark.asyncio
async def test_publication_changes_during_signing_do_not_return_old_cover(changed):
    value, client = authority()
    client.preview.side_effect = [preview(), changed]
    with pytest.raises(AppError) as caught:
        await value.preview("owner", "reading")
    assert caught.value.code == "CONTENT_VERSION_CONFLICT"


@pytest.mark.asyncio
async def test_module_or_account_change_after_signing_prevents_returning_cover():
    value, _ = authority()
    value.access.side_effect = AppError("CONTENT_ACCESS_DENIED", "Unavailable", 403)
    with pytest.raises(AppError) as caught:
        await value.preview("owner", "reading")
    assert caught.value.status_code == 403


@pytest.mark.asyncio
async def test_cover_that_expires_during_preview_recheck_is_rejected(monkeypatch):
    resource = signed()
    value, client = authority(resource=resource)
    now = datetime.now(UTC)
    clock = Mock()
    clock.now.side_effect = [now, now + timedelta(seconds=50)]
    monkeypatch.setattr("juya_miniapp_api.modules.short_reading.authority.datetime", clock)
    with pytest.raises(AppError):
        await value.preview("owner", "reading")
    assert client.preview.await_count == 2
