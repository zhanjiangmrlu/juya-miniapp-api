from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from juya_miniapp_api.integrations.admin_api.client import _validate
from juya_miniapp_api.integrations.admin_api.schemas import SignedResource
from juya_miniapp_api.integrations.admin_api.v15_client import AdminV15Client
from juya_miniapp_api.integrations.admin_api.v15_content import SceneDetail
from juya_miniapp_api.integrations.admin_api.v15_guard import scene_revision
from juya_miniapp_api.shared.errors import AppError


class V15SceneAuthority:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], client: AdminV15Client) -> None:
        self.sessions = sessions
        self.client = client

    async def is_v15(self, scene_id: str) -> bool:
        async with self.sessions() as session:
            _, _, content = await scene_revision(session, scene_id)
        return content.schema_version == 2

    async def open_scene(self, user_id: str, scene_id: str) -> SceneDetail:
        async with self.sessions() as session:
            _, _, content = await scene_revision(session, scene_id)
        kind: Literal["dialogue", "word_card"] = (
            "word_card" if content.template_type in {"word_card", "vocabulary"} else "dialogue"
        )
        detail = await self.client.scene_detail(user_id, kind, scene_id)
        if detail.content_id != scene_id or detail.content_type != kind:
            raise AppError("CONTENT_CONTRACT_INVALID", "内容服务返回不一致", 503)
        return detail

    async def signed_resource(
        self, user_id: str, scene_id: str, target_id: str, revision_id: str, now: datetime
    ) -> SignedResource:
        detail = await self.open_scene(user_id, scene_id)
        if detail.revision_id != revision_id:
            raise AppError("CONTENT_VERSION_CONFLICT", "内容资源已更新,请刷新", 409)
        references: list[tuple[str, str | None]] = []
        if detail.whole_audio:
            references.append((detail.whole_audio.target_id, detail.whole_audio.resource_version))
        references.extend(
            (identity, None)
            for identity in [detail.cover_asset_id, detail.learning_image_asset_id]
            if identity
        )
        for sentence in detail.dialogue:
            if sentence.slice_target_id and sentence.slice_version:
                references.append((sentence.slice_target_id, sentence.slice_version))
        for entry in detail.words + detail.chunks:
            if entry.image_asset_id and entry.entry_type == "word":
                references.append((entry.image_asset_id, None))
            if entry.pronunciation and entry.pronunciation.resource_version:
                references.append(
                    (entry.pronunciation.target_id, entry.pronunciation.resource_version)
                )
        versions = {version for identity, version in references if identity == target_id}
        if len(versions) != 1:
            raise AppError("RESOURCE_REFERENCE_INVALID", "资源不属于当前授权版本", 403)
        payload = await self.client.resource(
            user_id, detail.content_type, scene_id, target_id, versions.pop()
        )
        result = _validate(
            SignedResource,
            {
                "resource_id": payload.get("target_id", target_id),
                "url": payload.get("url"),
                "expires_at": payload.get("expires_at"),
            },
        )
        if result.resource_id != target_id or now >= result.expires_at.astimezone(UTC):
            raise AppError("SIGNED_MEDIA_EXPIRED", "媒体地址已失效", 503)
        return result
