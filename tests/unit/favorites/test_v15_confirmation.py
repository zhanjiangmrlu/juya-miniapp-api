import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from juya_miniapp_api.modules.favorites.snapshots import SnapshotReference
from juya_miniapp_api.shared.errors import AppError


@pytest.mark.asyncio
async def test_actual_confirmation_route_excludes_client_data_and_transient_credential() -> None:
    from juya_miniapp_api.integrations.admin_api.v15_client import AdminV15Client

    calls = []
    ref = SnapshotReference(
        content_type="word_card",
        content_id="card",
        content_version=1,
        entry_id="medium",
        sense_id="cup",
        source_id="source",
    )
    now = datetime.now(UTC)
    credential = {
        **ref.model_dump(exclude={"entry_type"}),
        "user_id": "user",
        "revision_id": "revision",
        "details_hash": "hash",
        "module_version": 1,
        "authorization_version": "auth",
        "expires_at": (now + timedelta(seconds=60)).isoformat(),
    }
    raw = (
        base64.urlsafe_b64encode(
            json.dumps(credential, sort_keys=True, separators=(",", ":")).encode()
        )
        .decode()
        .rstrip("=")
    )
    token = raw + "." + hmac.new(b"x" * 32, raw.encode(), hashlib.sha256).hexdigest()

    def respond(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "credential": {**credential, "token": token},
                "entry": {
                    "entry_id": "medium",
                    "entry_type": "word",
                    "sense_id": "cup",
                    "source_id": "source",
                    "english": "medium",
                    "meaning_zh": "中杯",
                    "source_sentence": "A medium coffee.",
                    "source_locator": {"sentence_id": "s1"},
                },
                "safe_summary": {"title_en": "Coffee", "title_zh": "咖啡店"},
            },
        )

    async with httpx.AsyncClient(
        base_url="http://admin", transport=httpx.MockTransport(respond)
    ) as http:
        client = AdminV15Client(http, secret=b"x" * 32)
        result = await client.confirm_snapshot("user", ref)
        assert calls[0].url.path == "/internal/v1/favorite-snapshots/confirmation"
        assert json.loads(calls[0].content) == {
            "user_id": "user",
            "reference": ref.model_dump(exclude={"entry_type"}),
        }
        assert result.reference.entry_type == "word"
        assert result._confirmation_credential == {**credential, "token": token}
        assert "token" not in result.model_dump_json()


def test_confirmation_credential_detects_tampering_and_expiry() -> None:
    from juya_miniapp_api.integrations.admin_api.v15_guard import verify_credential

    now = datetime.now(UTC)
    claims = {"expires_at": (now + timedelta(seconds=10)).isoformat()}
    raw = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    token = raw + "." + hmac.new(b"x" * 32, raw.encode(), hashlib.sha256).hexdigest()
    assert verify_credential(token, b"x" * 32, now) == claims
    with pytest.raises(AppError):
        verify_credential(token + "0", b"x" * 32, now)
    with pytest.raises(AppError):
        verify_credential(token, b"x" * 32, now + timedelta(seconds=10))


@pytest.mark.asyncio
async def test_final_guard_rechecks_expiry_after_fact_lock_wait(monkeypatch) -> None:
    from juya_miniapp_api.integrations.admin_api import v15_guard
    from juya_miniapp_api.integrations.admin_api.content import SceneContent
    from juya_miniapp_api.integrations.admin_api.v15_facts import AccessFacts
    from juya_miniapp_api.modules.favorites.snapshots import ConfirmedSnapshot, request_digest

    now = datetime.now(UTC)
    current = [now]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current[0]

    monkeypatch.setattr(v15_guard, "datetime", Clock)
    ref = SnapshotReference(
        content_type="word_card",
        content_id="card",
        content_version=1,
        entry_type="word",
        entry_id="entry",
        sense_id="sense",
        source_id="source",
    )
    data = ConfirmedSnapshot(
        reference=ref,
        revision_id="r1",
        english="medium",
        meaning_zh="中杯",
        source_sentence="Coffee.",
        source_title="Coffee",
        source_locator={},
    )
    content = SceneContent(
        schema_version=2,
        template_type="word_card",
        title_en="Coffee",
        vocabulary=[
            {
                "entry_id": "entry",
                "sense_id": "sense",
                "source_id": "source",
                "english": "medium",
                "chinese": "中杯",
                "source_sentence": "Coffee.",
                "confirmed": True,
            }
        ],
    )
    entry = v15_guard.confirmed_entry(content, data)
    claims = {
        **ref.model_dump(exclude={"entry_type"}),
        "user_id": "user",
        "revision_id": "r1",
        "details_hash": request_digest(
            entry.model_dump(mode="json", exclude={"is_favorited", "favorite_id"})
        ),
        "module_version": 1,
        "authorization_version": "auth",
        "expires_at": (now + timedelta(seconds=1)).isoformat(),
    }
    raw = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    token = raw + "." + hmac.new(b"x" * 32, raw.encode(), hashlib.sha256).hexdigest()
    data._confirmation_credential = {**claims, "token": token}
    guard = v15_guard.FinalFactsGuard(b"x" * 32)

    async def authorizer(*args, **kwargs):
        current[0] = now + timedelta(seconds=2)
        return AccessFacts(True, True, 1, "auth")

    async def revision(*args, **kwargs):
        return "r1", 1, content

    guard.authorizer = authorizer
    monkeypatch.setattr(v15_guard, "scene_revision", revision)
    with pytest.raises(AppError, match="凭据"):
        await guard.snapshot(None, "user", data)
