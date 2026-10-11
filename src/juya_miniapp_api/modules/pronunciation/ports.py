from typing import Any, Protocol

from juya_miniapp_api.integrations.admin_api.v15_content import SceneDetail
from juya_miniapp_api.modules.pronunciation.domain import PronunciationRequest


class SnapshotContextPort(Protocol):
    async def target_for_review(self, user_id: str, favorite_id: str) -> str: ...

    async def review_context(
        self, user_id: str, favorite_id: str, target_id: str
    ) -> dict[str, object]: ...


class CurrentContentPort(Protocol):
    async def pronunciation_context(
        self, user_id: str, reference: PronunciationRequest, target_id: str | None
    ) -> dict[str, object]: ...


class CurrentDetailPort(Protocol):
    """Must recheck account, module, current publication and entitlement per call."""

    async def open_scene(self, user_id: str, content_id: str) -> SceneDetail: ...


class PronunciationClient(Protocol):
    """Authenticated internal client; poll must match the authorized target/cache key."""

    async def resolve(self, target_id: str, context: dict[str, object]) -> dict[str, Any]: ...

    async def poll(self, task_id: str, context: dict[str, object]) -> dict[str, Any]: ...
