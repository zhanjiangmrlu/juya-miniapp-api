"""Independent immutable sense/source snapshots; never key by English spelling."""

import asyncio
import base64
import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from juya_miniapp_api.shared.errors import AppError
from juya_miniapp_api.shared.ids import new_ulid

EntryType = Literal["word", "chunk"]
SnapshotIdentity = tuple[str, str, str, str, str, str]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SnapshotReference(StrictModel):
    content_type: Literal["dialogue", "word_card", "SCENE"]
    content_id: str = Field(min_length=1, max_length=64)
    content_version: int | None = Field(default=None, ge=1)
    entry_type: EntryType | None = None
    entry_id: str = Field(min_length=1, max_length=64)
    sense_id: str = Field(min_length=1, max_length=64)
    source_id: str = Field(min_length=1, max_length=64)

    def identity(self) -> SnapshotIdentity:
        if self.entry_type is None:
            raise ValueError("Entry type must come from a confirmed entry")
        return (
            self.content_type,
            self.content_id,
            self.entry_type,
            self.entry_id,
            self.sense_id,
            self.source_id,
        )


class PronunciationReference(StrictModel):
    target_id: str = Field(min_length=1, max_length=64)
    resource_version: str | None = None
    can_prepare: bool = False


class ConfirmedSnapshot(StrictModel):
    _confirmation_credential: dict[str, Any] | None = PrivateAttr(default=None)
    reference: SnapshotReference
    revision_id: str | None = Field(default=None, min_length=1, max_length=64)
    english: str = Field(max_length=10000)
    phonetic: str = Field(default="", max_length=200)
    meaning_zh: str = Field(max_length=10000)
    explanation: str = Field(default="", max_length=10000)
    source_sentence: str = Field(max_length=10000)
    source_title: str = Field(max_length=200)
    source_locator: dict[str, str | int]
    image_asset_id: str | None = None
    pronunciation: PronunciationReference | None = None
    legacy_incomplete: bool = False
    missing_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def chunk_has_no_image(self) -> "ConfirmedSnapshot":
        if self.reference.entry_type is None:
            raise ValueError("Confirmed snapshot requires an authoritative entry type")
        if not self.legacy_incomplete and (
            not self.english
            or not self.meaning_zh
            or self.revision_id is None
            or self.reference.content_version is None
            or self.reference.content_type == "SCENE"
        ):
            raise ValueError("New confirmed snapshots require complete authority")
        if self.reference.entry_type == "chunk" and self.image_asset_id is not None:
            raise ValueError("Chunk snapshots cannot carry display images")
        return self


class FavoriteSnapshot(StrictModel):
    favorite_id: str
    active: bool
    snapshot_version: int = Field(ge=1)
    snapshot_revision_id: str | None = None
    saved_content_version: int | None = Field(default=None, ge=1)
    favorited_at: datetime
    last_reviewed_at: datetime | None = None
    data: ConfirmedSnapshot
    schema_version: Literal[2] = 2


class SnapshotPage(StrictModel):
    items: list[FavoriteSnapshot]
    next_cursor: str | None
    has_more: bool


class SnapshotCreateReference(SnapshotReference):
    content_type: Literal["dialogue", "word_card"]
    content_version: int = Field(ge=1)


class SnapshotReview(StrictModel):
    id: str
    snapshot_schema: Literal[2] = 2
    card_ids: list[str]
    snapshot_versions: list[int]
    card_count: int
    started_at: datetime
    completed_at: datetime | None = None


class SnapshotAuthority(Protocol):
    async def confirm_snapshot(self, user_id: str, ref: SnapshotReference) -> ConfirmedSnapshot: ...


class SnapshotRepository(Protocol):
    async def replay(
        self, user_id: str, action: str, key: str, request_hash: str
    ) -> FavoriteSnapshot | None: ...

    async def create(
        self,
        user_id: str,
        ref: SnapshotReference,
        key: str,
        now: datetime,
        confirm: Callable[[], Awaitable[ConfirmedSnapshot]],
    ) -> FavoriteSnapshot: ...

    async def cancel(
        self, user_id: str, favorite_id: str, key: str, now: datetime
    ) -> FavoriteSnapshot: ...

    async def get(self, user_id: str, favorite_id: str) -> FavoriteSnapshot | None: ...

    async def list_active(
        self, user_id: str, entry_type: EntryType | None, after: str | None, limit: int
    ) -> list[FavoriteSnapshot]: ...

    async def states(
        self, user_id: str, identities: list[SnapshotIdentity]
    ) -> dict[SnapshotIdentity, str]: ...


def request_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


class InMemorySnapshotRepository:
    """Behavior test adapter. Production uses the admin-owned SQL schema."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, SnapshotIdentity], FavoriteSnapshot] = {}
        self.operations: dict[tuple[str, str, str], tuple[str, FavoriteSnapshot]] = {}
        self.events: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()

    async def replay(
        self, user_id: str, action: str, key: str, request_hash: str
    ) -> FavoriteSnapshot | None:
        saved = self.operations.get((user_id, action, key))
        if saved is None:
            return None
        if saved[0] != request_hash:
            raise AppError("IDEMPOTENCY_CONFLICT", "幂等键已用于其他操作", 409)
        return saved[1]

    async def create(
        self,
        user_id: str,
        ref: SnapshotReference,
        key: str,
        now: datetime,
        confirm: Callable[[], Awaitable[ConfirmedSnapshot]],
    ) -> FavoriteSnapshot:
        digest = request_digest(ref.model_dump())
        async with self._lock:
            replay = await self.replay(user_id, "create", key, digest)
            if replay:
                return replay
            data = await confirm()
            if (
                data.reference.model_dump()
                != ref.model_copy(
                    update={"entry_type": ref.entry_type or data.reference.entry_type}
                ).model_dump()
            ):
                raise AppError("CONTENT_VERSION_CONFLICT", "内容已更新,请刷新后收藏", 409)
            ref = data.reference
            identity = user_id, ref.identity()
            current = self.rows.get(identity)
            if current is None or not current.active:
                row = FavoriteSnapshot(
                    favorite_id=current.favorite_id if current else new_ulid(now),
                    active=True,
                    snapshot_version=current.snapshot_version + 1 if current else 1,
                    saved_content_version=ref.content_version,
                    favorited_at=now,
                    data=data,
                )
                self.rows[identity] = row
                self.events.append(
                    {
                        "favorite_id": row.favorite_id,
                        "active": True,
                        "snapshot_version": row.snapshot_version,
                    }
                )
            else:
                row = current
            self.operations[user_id, "create", key] = digest, row
            return row

    async def cancel(
        self, user_id: str, favorite_id: str, key: str, now: datetime
    ) -> FavoriteSnapshot:
        del now
        digest = request_digest(favorite_id)
        async with self._lock:
            replay = await self.replay(user_id, "cancel", key, digest)
            if replay:
                return replay
            current = await self.get(user_id, favorite_id)
            if current is None:
                raise AppError("FAVORITE_NOT_FOUND", "收藏不存在", 404)
            row = current.model_copy(update={"active": False})
            self.rows[user_id, row.data.reference.identity()] = row
            if current.active:
                self.events.append(
                    {
                        "favorite_id": row.favorite_id,
                        "active": False,
                        "snapshot_version": row.snapshot_version,
                    }
                )
            self.operations[user_id, "cancel", key] = digest, row
            return row

    async def get(self, user_id: str, favorite_id: str) -> FavoriteSnapshot | None:
        return next(
            (
                row
                for (owner, _), row in self.rows.items()
                if owner == user_id and row.favorite_id == favorite_id
            ),
            None,
        )

    async def list_active(
        self, user_id: str, entry_type: EntryType | None, after: str | None, limit: int
    ) -> list[FavoriteSnapshot]:
        return sorted(
            (
                row
                for (owner, _), row in self.rows.items()
                if owner == user_id
                and row.active
                and (after is None or row.favorite_id > after)
                and (entry_type is None or row.data.reference.entry_type == entry_type)
            ),
            key=lambda row: row.favorite_id,
        )[:limit]

    async def states(
        self, user_id: str, identities: list[SnapshotIdentity]
    ) -> dict[SnapshotIdentity, str]:
        selected = set(identities)
        return {
            identity: row.favorite_id
            for (owner, identity), row in self.rows.items()
            if owner == user_id and row.active and identity in selected
        }


class SnapshotService:
    def __init__(self, repository: SnapshotRepository, authority: SnapshotAuthority) -> None:
        self.repository = repository
        self.authority = authority

    async def create(
        self, user_id: str, ref: SnapshotReference, key: str, now: datetime
    ) -> FavoriteSnapshot:
        self._key(key)
        if ref.content_type == "SCENE" or ref.content_version is None:
            raise AppError("CONTENT_REFERENCE_INVALID", "收藏须引用当前确认版本", 422)

        # Repository owns the atomic replay/confirmation/write boundary.
        async def confirm() -> ConfirmedSnapshot:
            return await self.authority.confirm_snapshot(user_id, ref)

        return await self.repository.create(user_id, ref, key, now, confirm)

    async def cancel(
        self, user_id: str, favorite_id: str, key: str, now: datetime
    ) -> FavoriteSnapshot:
        self._key(key)
        return await self.repository.cancel(user_id, favorite_id, key, now)

    async def detail(self, user_id: str, favorite_id: str) -> FavoriteSnapshot:
        row = await self.repository.get(user_id, favorite_id)
        if row is None or not row.active:
            raise AppError("FAVORITE_NOT_FOUND", "收藏不存在", 404)
        return row

    async def review_context(
        self, user_id: str, favorite_id: str, target_id: str
    ) -> dict[str, object]:
        row = await self.detail(user_id, favorite_id)
        pronunciation = row.data.pronunciation
        if pronunciation is None or pronunciation.target_id != target_id:
            raise AppError("PRONUNCIATION_ACCESS_DENIED", "此收藏没有对应发音", 403)
        return {
            "purpose": "FAVORITE_REVIEW",
            "user_id": user_id,
            "favorite_id": favorite_id,
            "snapshot_version": row.snapshot_version,
            "target_id": target_id,
            "resource_version": pronunciation.resource_version,
            "content_type": row.data.reference.content_type,
            "content_id": row.data.reference.content_id,
            "content_version": row.saved_content_version,
            "entry_id": row.data.reference.entry_id,
            "sense_id": row.data.reference.sense_id,
            "source_id": row.data.reference.source_id,
        }

    async def page(
        self, user_id: str, entry_type: EntryType | None, cursor: str | None, limit: int
    ) -> SnapshotPage:
        if not 1 <= limit <= 100:
            raise AppError("PAGE_LIMIT_INVALID", "分页数量不正确", 422)
        scope = request_digest([user_id, entry_type, 2])
        after = None
        if cursor is not None:
            try:
                parsed = json.loads(base64.urlsafe_b64decode(cursor.encode()))
                if parsed["scope"] != scope or not isinstance(parsed["after"], str):
                    raise ValueError("Cursor scope mismatch")
                after = parsed["after"]
            except (ValueError, KeyError, TypeError) as error:
                raise AppError("CURSOR_INVALID", "分页位置已失效,请刷新", 422) from error
        rows = await self.repository.list_active(user_id, entry_type, after, limit + 1)
        has_more = len(rows) > limit
        visible = rows[:limit]
        next_cursor = None
        if has_more:
            next_cursor = base64.urlsafe_b64encode(
                json.dumps({"scope": scope, "after": visible[-1].favorite_id}).encode()
            ).decode()
        return SnapshotPage(items=visible, has_more=has_more, next_cursor=next_cursor)

    @staticmethod
    def _key(key: str) -> None:
        if not key.strip() or len(key) > 128:
            raise AppError("IDEMPOTENCY_KEY_INVALID", "请提供有效幂等键", 422)
