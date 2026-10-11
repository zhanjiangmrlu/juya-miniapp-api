from juya_miniapp_api.modules.favorites.snapshots import SnapshotService
from juya_miniapp_api.modules.pronunciation.domain import PronunciationRequest
from juya_miniapp_api.modules.pronunciation.ports import CurrentDetailPort
from juya_miniapp_api.shared.errors import AppError


def forbidden() -> AppError:
    return AppError("PRONUNCIATION_ACCESS_DENIED", "无权访问此发音目标", 403)


class SnapshotReviewAdapter:
    """Reads authoritative active snapshots independently of source entitlement."""

    def __init__(self, snapshots: SnapshotService) -> None:
        self.snapshots = snapshots

    async def target_for_review(self, user_id: str, favorite_id: str) -> str:
        row = await self.snapshots.detail(user_id, favorite_id)
        pronunciation = row.data.pronunciation
        if pronunciation is None:
            raise forbidden()
        return pronunciation.target_id

    async def review_context(
        self,
        user_id: str,
        favorite_id: str,
        target_id: str,
    ) -> dict[str, object]:
        context = await self.snapshots.review_context(user_id, favorite_id, target_id)
        # Detail repeats active/owner validation if cancellation raced with target lookup.
        row = await self.snapshots.detail(user_id, favorite_id)
        pronunciation = row.data.pronunciation
        if pronunciation is None or pronunciation.target_id != target_id:
            raise forbidden()
        # Use the second authoritative read as a whole; never mix snapshot revisions.
        ref = row.data.reference
        context.update(
            {
                "purpose": "FAVORITE_REVIEW",
                "user_id": user_id,
                "favorite_id": row.favorite_id,
                "snapshot_version": row.snapshot_version,
                "snapshot_revision_id": row.snapshot_revision_id,
                "content_type": ref.content_type,
                "content_id": ref.content_id,
                "content_version": row.saved_content_version,
                "entry_id": ref.entry_id,
                "sense_id": ref.sense_id,
                "source_id": ref.source_id,
                "target_id": target_id,
                "resource_version": pronunciation.resource_version,
            }
        )
        return context


class CurrentContentAdapter:
    """Authority supplies a fresh fully authorized detail; never use a public cache."""

    def __init__(self, authority: CurrentDetailPort) -> None:
        self.authority = authority

    async def pronunciation_context(
        self,
        user_id: str,
        reference: PronunciationRequest,
        target_id: str | None,
    ) -> dict[str, object]:
        if reference.content_type not in ("dialogue", "word_card") or not reference.content_id:
            raise forbidden()
        detail = await self.authority.open_scene(user_id, reference.content_id)
        if (
            detail.content_id != reference.content_id
            or detail.content_type != reference.content_type
        ):
            raise AppError("CONTENT_CONTRACT_INVALID", "内容服务返回不一致", 503)
        if detail.content_version != reference.content_version:
            raise AppError("CONTENT_VERSION_CONFLICT", "内容已更新,请刷新后播放", 409)
        entries = [
            row
            for row in [*detail.words, *detail.chunks]
            if row.entry_id == reference.entry_id
            and (reference.sense_id is None or row.sense_id == reference.sense_id)
            and (reference.source_id is None or row.source_id == reference.source_id)
        ]
        if len(entries) != 1 or entries[0].pronunciation is None:
            raise forbidden()
        pronunciation = entries[0].pronunciation
        if target_id is not None and pronunciation.target_id != target_id:
            raise forbidden()
        if reference.resource_version is not None and (
            pronunciation.resource_version != reference.resource_version
        ):
            raise AppError("CONTENT_VERSION_CONFLICT", "发音资源已更新,请刷新", 409)
        return {
            "purpose": "CONTENT",
            "user_id": user_id,
            "content_type": detail.content_type,
            "content_id": detail.content_id,
            "content_version": detail.content_version,
            "entry_id": entries[0].entry_id,
            "target_id": pronunciation.target_id,
            "resource_version": pronunciation.resource_version,
        }
