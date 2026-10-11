import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from juya_miniapp_api.integrations.admin_api.content import SceneContent
from juya_miniapp_api.integrations.admin_api.v15_content import ConfirmedEntry, SourceLocator
from juya_miniapp_api.integrations.admin_api.v15_facts import SQLAuthorizer
from juya_miniapp_api.modules.favorites.snapshots import (
    ConfirmedSnapshot,
    PronunciationReference,
    request_digest,
)
from juya_miniapp_api.modules.favorites.snapshots_sql import json_value
from juya_miniapp_api.shared.errors import AppError


def verify_credential(token: str, secret: bytes, now: datetime) -> dict[str, Any]:
    try:
        payload, signature = token.split(".")
        expected = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if not isinstance(claims, dict):
            raise ValueError("claims")
        expiry = datetime.fromisoformat(claims["expires_at"])
        if expiry.tzinfo is None or now >= expiry:
            raise ValueError("expired")
        return dict(claims)
    except (ValueError, KeyError, TypeError) as error:
        raise AppError(
            "CONFIRMATION_CREDENTIAL_INVALID", "收藏确认凭据无效或已过期", 409
        ) from error


async def scene_revision(
    session: AsyncSession, identity: str, *, lock: bool = False
) -> tuple[str, int, SceneContent]:
    row = (
        (
            await session.execute(
                text(
                    "SELECT r.public_id,r.version_no,r.content_snapshot FROM scene s "
                    "JOIN scene_revision r ON r.id=s.published_revision_id "
                    "WHERE s.public_id=:id AND s.status='PUBLISHED' AND r.status='PUBLISHED'"
                    + (" FOR SHARE" if lock else "")
                ),
                {"id": identity},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise AppError("CONTENT_NOT_PUBLISHED", "内容未发布", 404)
    content = SceneContent.model_validate(json_value(row["content_snapshot"]))
    return str(row["public_id"]), int(row["version_no"]), content


def confirmed_entry(content: SceneContent, data: ConfirmedSnapshot) -> ConfirmedEntry:
    ref = data.reference
    if ref.entry_type is None:
        raise AppError("ENTRY_IDENTITY_CONFLICT", "收藏缺少权威类型", 409)
    rows = content.vocabulary if ref.entry_type == "word" else content.chunks
    matches = [
        row
        for row in rows
        if (row.entry_id, row.sense_id, row.source_id)
        == (ref.entry_id, ref.sense_id, ref.source_id)
    ]
    if len(matches) != 1 or not matches[0].confirmed:
        raise AppError("ENTRY_IDENTITY_CONFLICT", "本篇义项或来源不匹配", 409)
    row = matches[0]
    return ConfirmedEntry(
        entry_id=row.entry_id,
        entry_type=ref.entry_type,
        sense_id=row.sense_id,
        source_id=row.source_id,
        entry_version=row.entry_version,
        english=row.english,
        phonetic=row.phonetic,
        meaning_zh=row.chinese,
        explanation=row.explanation,
        source_sentence=row.source_sentence,
        source_locator=SourceLocator.model_validate(row.source_locator)
        if row.source_locator
        else None,
        image_asset_id=row.icon_asset_id,
        pronunciation=PronunciationReference(
            target_id=row.pronunciation_target_id,
            can_prepare=row.allow_synthesis,
            resource_version=row.audio_version_id,
        )
        if row.pronunciation_target_id
        else None,
    )


class FinalFactsGuard:
    def __init__(self, credential_secret: bytes) -> None:
        if len(credential_secret) < 16:
            raise ValueError("credential key must have at least 16 bytes")
        self.secret = credential_secret
        self.authorizer = SQLAuthorizer()

    async def snapshot(self, session: AsyncSession, user_id: str, data: ConfirmedSnapshot) -> None:
        now = datetime.now(UTC)
        credential = data._confirmation_credential
        if credential is None:
            raise AppError("CONFIRMATION_CREDENTIAL_INVALID", "缺少权威收藏确认凭据", 409)
        claims = verify_credential(str(credential["token"]), self.secret, now)
        if claims != {key: value for key, value in credential.items() if key != "token"}:
            raise AppError("CONFIRMATION_CREDENTIAL_INVALID", "确认凭据与资料不一致", 409)
        ref = data.reference
        if ref.content_type == "SCENE" or ref.content_version is None or data.legacy_incomplete:
            raise AppError("CONTENT_REFERENCE_INVALID", "收藏须引用当前完整确认资料", 422)
        if any(
            claims.get(key) != value
            for key, value in {"user_id": user_id, **ref.model_dump(exclude={"entry_type"})}.items()
        ):
            raise AppError("CONFIRMATION_CREDENTIAL_INVALID", "收藏确认身份不一致", 409)
        facts = await self.authorizer(
            session, user_id, ref.content_type, ref.content_id, now, lock=True
        )
        if not facts.full_access:
            raise AppError("CONTENT_ACCESS_DENIED", "当前账号暂未开通此内容", 403)
        revision, version, content = await scene_revision(session, ref.content_id, lock=True)
        locked_now = datetime.now(UTC)
        verify_credential(str(credential["token"]), self.secret, locked_now)
        if facts.expires_at is not None and locked_now >= facts.expires_at:
            raise AppError("CONTENT_ACCESS_DENIED", "当前账号暂未开通此内容", 403)
        kind = "word_card" if content.template_type in {"word_card", "vocabulary"} else "dialogue"
        entry = confirmed_entry(content, data)
        entry_dict = entry.model_dump(mode="json", exclude={"is_favorited", "favorite_id"})
        if (
            content.schema_version != 2
            or kind != ref.content_type
            or version != ref.content_version
            or revision != claims["revision_id"]
            or data.revision_id != revision
            or request_digest(entry_dict) != claims["details_hash"]
            or facts.module_version != claims["module_version"]
            or facts.authorization_version != claims["authorization_version"]
        ):
            raise AppError("CONTENT_VERSION_CONFLICT", "内容或授权已变化,请刷新", 409)
        # Bind the body actually saved, including safe titles, to the locked authoritative revision.
        if (
            data.english,
            data.phonetic,
            data.meaning_zh,
            data.explanation,
            data.source_sentence,
            data.source_locator,
            data.image_asset_id,
            data.pronunciation,
            data.source_title,
        ) != (
            entry.english,
            entry.phonetic,
            entry.meaning_zh,
            entry.explanation,
            entry.source_sentence,
            entry.source_locator.model_dump(exclude_none=True) if entry.source_locator else {},
            entry.image_asset_id,
            entry.pronunciation,
            content.title_zh or content.title_en,
        ):
            raise AppError("CONFIRMATION_CREDENTIAL_INVALID", "固定资料与权威条目不一致", 409)

    async def reading(
        self, session: AsyncSession, user_id: str, identity: str, version: int | None
    ) -> None:
        facts = await self.authorizer(
            session, user_id, "short_reading", identity, datetime.now(UTC), lock=True
        )
        if not facts.full_access:
            raise AppError("CONTENT_ACCESS_DENIED", "当前账号暂未开通此内容", 403)
        current = await session.scalar(
            text(
                "SELECT r.version_no FROM short_reading s JOIN short_reading_revision r "
                "ON r.public_id=s.published_revision_id WHERE s.public_id=:id "
                "AND s.status='PUBLISHED' AND r.status='PUBLISHED' FOR SHARE"
            ),
            {"id": identity},
        )
        if current is None or (version is not None and current != version):
            raise AppError("CONTENT_VERSION_CONFLICT", "短文已更新,请重新打开", 409)
        if facts.expires_at is not None and datetime.now(UTC) >= facts.expires_at:
            raise AppError("CONTENT_ACCESS_DENIED", "当前账号暂未开通此内容", 403)

    async def scene_kind(
        self, session: AsyncSession, identity: str
    ) -> Literal["dialogue", "word_card"]:
        _, _, content = await scene_revision(session, identity)
        return "word_card" if content.template_type in {"word_card", "vocabulary"} else "dialogue"
