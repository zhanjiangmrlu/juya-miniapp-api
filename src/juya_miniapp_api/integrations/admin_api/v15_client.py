"""HMAC consumer of the actual additive admin V1.5 routes."""

from typing import Any, Literal
from urllib.parse import quote

from pydantic import Field

from juya_miniapp_api.integrations.admin_api.client import AdminApiClient, _validate
from juya_miniapp_api.integrations.admin_api.v15_content import (
    ConfirmedEntry,
    SceneDetail,
    ShortReadingDetail,
    SourceResolution,
)
from juya_miniapp_api.modules.favorites.snapshots import (
    ConfirmedSnapshot,
    SnapshotReference,
    StrictModel,
)

ContentType = Literal["dialogue", "word_card", "short_reading"]


class ConfirmationCredential(StrictModel):
    content_type: ContentType
    content_id: str
    content_version: int = Field(ge=1)
    entry_id: str
    sense_id: str
    source_id: str
    user_id: str
    revision_id: str
    details_hash: str
    module_version: int
    authorization_version: str
    expires_at: str
    token: str


class Confirmation(StrictModel):
    credential: ConfirmationCredential
    entry: ConfirmedEntry
    safe_summary: dict[str, str]


def segment(value: str) -> str:
    return quote(value, safe="")


class AdminV15Client(AdminApiClient):
    async def content(self, user_id: str, kind: ContentType, identity: str) -> dict[str, Any]:
        return await self._request_json(
            "POST", f"/internal/v1/content/{kind}/{segment(identity)}/open", {"user_id": user_id}
        )

    async def scene_detail(
        self, user_id: str, kind: Literal["dialogue", "word_card"], identity: str
    ) -> SceneDetail:
        return _validate(SceneDetail, await self.content(user_id, kind, identity))

    async def reading_detail(self, user_id: str, identity: str) -> ShortReadingDetail:
        return _validate(ShortReadingDetail, await self.content(user_id, "short_reading", identity))

    async def preview(self, user_id: str, kind: ContentType, identity: str) -> dict[str, Any]:
        return await self._request_json(
            "POST", f"/internal/v1/content/{kind}/{segment(identity)}/preview", {"user_id": user_id}
        )

    async def confirm_snapshot(self, user_id: str, ref: SnapshotReference) -> ConfirmedSnapshot:
        result = _validate(
            Confirmation,
            await self._request_json(
                "POST",
                "/internal/v1/favorite-snapshots/confirmation",
                {"user_id": user_id, "reference": ref.model_dump(exclude={"entry_type"})},
            ),
        )
        entry = result.entry
        credential = result.credential
        confirmed = ConfirmedSnapshot(
            reference=SnapshotReference(
                **credential.model_dump(
                    include={
                        "content_type",
                        "content_id",
                        "content_version",
                        "entry_id",
                        "sense_id",
                        "source_id",
                    }
                ),
                entry_type=entry.entry_type,
            ),
            revision_id=credential.revision_id,
            english=entry.english,
            phonetic=entry.phonetic,
            meaning_zh=entry.meaning_zh,
            explanation=entry.explanation,
            source_sentence=entry.source_sentence,
            source_locator=entry.source_locator.model_dump(exclude_none=True)
            if entry.source_locator
            else {},
            source_title=result.safe_summary.get("title_zh")
            or result.safe_summary.get("title_en", ""),
            image_asset_id=entry.image_asset_id,
            pronunciation=entry.pronunciation,
        )
        confirmed._confirmation_credential = credential.model_dump()
        return confirmed

    async def source_resolution(self, user_id: str, favorite_id: str) -> SourceResolution:
        return _validate(
            SourceResolution,
            await self._request_json(
                "POST",
                f"/internal/v1/favorite-snapshots/{segment(favorite_id)}/source-resolution",
                {"user_id": user_id},
            ),
        )

    async def resource(
        self, user_id: str, kind: ContentType, identity: str, target: str, version: str | None
    ) -> dict[str, Any]:
        return await self._request_json(
            "POST",
            f"/internal/v1/content/{kind}/{segment(identity)}/resources/{segment(target)}/signed-url",
            {"user_id": user_id, "resource_version": version},
        )

    async def resolve(self, target_id: str, context: dict[str, object]) -> dict[str, Any]:
        return await self._request_json(
            "POST", f"/internal/v1/pronunciations/{segment(target_id)}/resolve", context
        )

    async def poll(self, task_id: str, context: dict[str, object]) -> dict[str, Any]:
        return await self._request_json(
            "POST", f"/internal/v1/pronunciations/tasks/{segment(task_id)}", context
        )
