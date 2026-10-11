from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from juya_miniapp_api.modules.pronunciation.domain import (
    PronunciationRequest,
    PronunciationResponse,
)
from juya_miniapp_api.modules.pronunciation.ports import (
    CurrentContentPort,
    PronunciationClient,
    SnapshotContextPort,
)
from juya_miniapp_api.shared.errors import AppError

_INTERNAL_FIELDS = frozenset(
    {
        "purpose",
        "user_id",
        "content_type",
        "content_id",
        "content_version",
        "entry_id",
        "favorite_id",
        "snapshot_version",
        "snapshot_revision_id",
        "target_id",
        "resource_version",
    }
)
_UNAVAILABLE_CODES = frozenset(
    {
        "PRONUNCIATION_UNAVAILABLE",
        "TTS_DISABLED",
        "TTS_TEXT_LIMIT",
        "PRONUNCIATION_BUDGET_EXCEEDED",
        "TTS_BUDGET_EXCEEDED",
        "PRONUNCIATION_NOT_CONFIGURED",
    }
)


def contract_error() -> AppError:
    return AppError("PRONUNCIATION_CONTRACT_INVALID", "发音服务暂不可用", 503)


class PronunciationService:
    """Delegates only server-authorized target references to the internal service."""

    def __init__(
        self,
        snapshots: SnapshotContextPort,
        content: CurrentContentPort,
        client: PronunciationClient,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.snapshots = snapshots
        self.content = content
        self.client = client
        self.clock = clock

    async def resolve(
        self,
        user_id: str,
        target_id: str,
        reference: PronunciationRequest,
    ) -> PronunciationResponse:
        context = await self._context(user_id, reference, target_id)
        try:
            result = await self.client.resolve(target_id, context)
        except AppError as error:
            if error.code not in _UNAVAILABLE_CODES:
                raise
            return PronunciationResponse(status="UNAVAILABLE", code=error.code)
        return self._response(result, context)

    async def poll(
        self,
        user_id: str,
        task_id: str,
        reference: PronunciationRequest,
        target_id: str | None = None,
    ) -> PronunciationResponse:
        context = await self._context(user_id, reference, target_id)
        try:
            result = await self.client.poll(task_id, context)
        except AppError as error:
            if error.code not in _UNAVAILABLE_CODES:
                raise
            return PronunciationResponse(status="UNAVAILABLE", task_id=task_id, code=error.code)
        result_task = result.get("task_id")
        if result_task is not None and result_task != task_id:
            raise contract_error()
        return self._response({**result, "task_id": task_id}, context)

    async def _context(
        self,
        user_id: str,
        reference: PronunciationRequest,
        target_id: str | None,
    ) -> dict[str, object]:
        if reference.favorite_id is not None:
            target = target_id or await self.snapshots.target_for_review(
                user_id, reference.favorite_id
            )
            authoritative = await self.snapshots.review_context(
                user_id, reference.favorite_id, target
            )
            purpose = "FAVORITE_REVIEW"
        else:
            authoritative = await self.content.pronunciation_context(user_id, reference, target_id)
            purpose = "CONTENT"
        if (
            authoritative.get("purpose") != purpose
            or authoritative.get("user_id") != user_id
            or not isinstance(authoritative.get("target_id"), str)
            or not authoritative["target_id"]
            or (target_id is not None and authoritative["target_id"] != target_id)
        ):
            raise contract_error()
        # An optional public hint may constrain a fixed reference, never replace it.
        if reference.resource_version is not None and (
            authoritative.get("resource_version") != reference.resource_version
        ):
            raise AppError("CONTENT_VERSION_CONFLICT", "发音资源已更新,请刷新", 409)
        context = {
            key: value
            for key, value in authoritative.items()
            if key in _INTERNAL_FIELDS and value is not None
        }
        for version in ("content_version", "snapshot_version"):
            if version in context:
                context[version] = str(context[version])
        context["nonce"] = uuid4().hex
        return context

    def _response(
        self,
        result: dict[str, Any],
        context: dict[str, object],
    ) -> PronunciationResponse:
        status = result.get("status")
        code = result.get("code", result.get("error_code"))
        if code is not None and (not isinstance(code, str) or len(code) > 128):
            raise contract_error()
        task_id = result.get("task_id")
        if task_id is not None and (
            not isinstance(task_id, str) or not task_id or len(task_id) > 64
        ):
            raise contract_error()
        if status == "READY":
            url, version = result.get("url"), result.get("resource_version")
            if not isinstance(url, str) or not isinstance(version, str) or not version:
                raise contract_error()
            try:
                parsed = urlsplit(url)
                if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username:
                    raise ValueError("Invalid signed resource URL")
                expiry = self._expiry(result.get("expires_at"))
            except (ValueError, TypeError, OverflowError) as error:
                raise contract_error() from error
            if expiry <= self.clock() or (
                context.get("resource_version") is not None
                and context["resource_version"] != version
            ):
                raise contract_error()
            return PronunciationResponse(
                status="READY",
                url=url,
                resource_version=version,
                task_id=task_id,
                expires_at=expiry,
                code=code,
            )
        if status in ("PENDING", "PREPARING", "RETRY", "RUNNING", "RECOVER"):
            if task_id is None:
                raise contract_error()
            return PronunciationResponse(status="PREPARING", task_id=task_id, code=code)
        if status in ("FAILED", "UNKNOWN"):
            return PronunciationResponse(
                status="FAILED",
                task_id=task_id,
                can_retry=status == "FAILED" and result.get("can_retry") is True,
                code=code or ("PRONUNCIATION_RESULT_UNKNOWN" if status == "UNKNOWN" else None),
            )
        if status == "UNAVAILABLE":
            return PronunciationResponse(status="UNAVAILABLE", task_id=task_id, code=code)
        raise contract_error()

    @staticmethod
    def _expiry(value: object) -> datetime:
        if isinstance(value, bool):
            raise ValueError("Invalid expiry")
        if isinstance(value, datetime):
            expiry = value
        elif isinstance(value, int | float):
            expiry = datetime.fromtimestamp(value, UTC)
        elif isinstance(value, str):
            expiry = datetime.fromisoformat(value)
        else:
            raise ValueError("Missing expiry")
        if expiry.tzinfo is None:
            raise ValueError("Expiry must be timezone-aware")
        return expiry.astimezone(UTC)
