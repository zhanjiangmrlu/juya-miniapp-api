from typing import Any, Protocol

from juya_miniapp_api.modules.feedback.content_safety import ensure_safe_feedback
from juya_miniapp_api.shared.errors import AppError


class FeedbackClient(Protocol):
    async def create_feedback(
        self,
        *,
        user_id: str,
        category: str,
        description: str,
        source: dict[str, Any],
        screenshots: list[str],
        idempotency_key: str,
    ) -> dict[str, Any]: ...

    async def list_feedback(self, user_id: str) -> list[dict[str, Any]]: ...

    async def get_feedback(self, feedback_id: str) -> dict[str, Any]: ...

    async def supplement_feedback(
        self,
        feedback_id: str,
        user_id: str,
        text: str,
        idempotency_key: str,
        *,
        screenshots: list[str] | None = None,
    ) -> dict[str, Any]: ...

    async def resolve_feedback(
        self,
        feedback_id: str,
        user_id: str,
        action: str,
        reason: str | None,
        idempotency_key: str,
    ) -> dict[str, Any]: ...


class FeedbackService:
    def __init__(self, client: FeedbackClient, *, require_review: bool = False) -> None:
        self._client = client
        self._require_review = require_review

    async def create(
        self,
        user_id: str,
        category: str,
        description: str,
        source: dict[str, Any],
        screenshots: list[str],
        idempotency_key: str,
    ) -> dict[str, Any]:
        cleaned = description.strip()
        if not cleaned or len(cleaned) > 300:
            raise AppError("FEEDBACK_DESCRIPTION_INVALID", "反馈说明需为1至300字", 422)
        if self._require_review:
            ensure_safe_feedback(cleaned)
        if len(screenshots) > 1:
            raise AppError("FEEDBACK_SCREENSHOT_LIMIT", "每条反馈最多上传1张截图", 422)
        expected_prefix = f"feedback/{user_id}/"
        if any(not key.startswith(expected_prefix) for key in screenshots):
            raise AppError("FEEDBACK_SCREENSHOT_INVALID", "反馈截图无效", 422)
        return await self._client.create_feedback(
            user_id=user_id,
            category=category,
            description=cleaned,
            source=source,
            screenshots=screenshots,
            idempotency_key=idempotency_key,
        )

    async def list_feedback(self, user_id: str) -> list[dict[str, Any]]:
        return await self._client.list_feedback(user_id)

    async def detail(self, user_id: str, feedback_id: str) -> dict[str, Any]:
        item = await self._client.get_feedback(feedback_id)
        self._assert_owner(item, user_id)
        return item

    async def supplement(
        self,
        user_id: str,
        feedback_id: str,
        text: str,
        idempotency_key: str,
        *,
        screenshots: list[str] | None = None,
    ) -> dict[str, Any]:
        item = await self._client.get_feedback(feedback_id)
        self._assert_owner(item, user_id)
        cleaned = text.strip()
        if not cleaned or len(cleaned) > 300:
            raise AppError("FEEDBACK_SUPPLEMENT_INVALID", "补充内容需为1至300字", 422)
        if self._require_review:
            ensure_safe_feedback(cleaned)
        images = screenshots or []
        if len(images) > 1 or (images and item.get("screenshots")):
            raise AppError("FEEDBACK_SCREENSHOT_LIMIT", "每条反馈最多上传1张截图", 422)
        if any(not key.startswith(f"feedback/{user_id}/") for key in images):
            raise AppError("FEEDBACK_SCREENSHOT_INVALID", "反馈截图无效", 422)
        if images:
            return await self._client.supplement_feedback(
                feedback_id, user_id, cleaned, idempotency_key, screenshots=images
            )
        return await self._client.supplement_feedback(
            feedback_id, user_id, cleaned, idempotency_key
        )

    async def resolve(
        self,
        user_id: str,
        feedback_id: str,
        action: str,
        reason: str | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        item = await self._client.get_feedback(feedback_id)
        self._assert_owner(item, user_id)
        return await self._client.resolve_feedback(
            feedback_id, user_id, action, reason, idempotency_key
        )

    @staticmethod
    def _assert_owner(item: dict[str, Any], user_id: str) -> None:
        if item.get("user_id") != user_id:
            raise AppError("FEEDBACK_NOT_FOUND", "反馈不存在", 404)
