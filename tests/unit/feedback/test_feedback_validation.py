from datetime import UTC, datetime

import pytest

from juya_miniapp_api.integrations.oss.upload import OssUploadService
from juya_miniapp_api.modules.feedback.service import FeedbackService
from juya_miniapp_api.shared.errors import AppError

NOW = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)


class CapturingFeedbackClient:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []

    async def create_feedback(self, **payload: object) -> dict[str, object]:
        self.created.append(payload)
        return {"id": "feedback-1", "status": "PENDING", "created_at": NOW.isoformat()}


@pytest.mark.asyncio
async def test_feedback_only_accepts_one_screenshot_owned_by_current_user() -> None:
    client = CapturingFeedbackClient()
    service = FeedbackService(client)

    result = await service.create(
        "user-1",
        "CONTENT",
        "场景中有一处翻译不准确",
        {"page": "scene"},
        ["feedback/user-1/image.png"],
        "command-1",
    )

    assert result["id"] == "feedback-1"
    assert client.created == [
        {
            "user_id": "user-1",
            "category": "CONTENT",
            "description": "场景中有一处翻译不准确",
            "source": {"page": "scene"},
            "screenshots": ["feedback/user-1/image.png"],
            "idempotency_key": "command-1",
        }
    ]

    with pytest.raises(AppError) as too_many:
        await service.create(
            "user-1",
            "CONTENT",
            "描述",
            {},
            ["feedback/user-1/1.png", "feedback/user-1/2.png"],
            "command-2",
        )
    assert too_many.value.code == "FEEDBACK_SCREENSHOT_LIMIT"

    with pytest.raises(AppError) as foreign_key:
        await service.create(
            "user-1",
            "CONTENT",
            "描述",
            {},
            ["feedback/user-2/image.png"],
            "command-3",
        )
    assert foreign_key.value.code == "FEEDBACK_SCREENSHOT_INVALID"


@pytest.mark.asyncio
async def test_feedback_description_is_trimmed_and_limited_to_300_characters() -> None:
    service = FeedbackService(CapturingFeedbackClient())

    with pytest.raises(AppError) as empty:
        await service.create("user-1", "OTHER", "   ", {}, [], "command-1")
    assert empty.value.code == "FEEDBACK_DESCRIPTION_INVALID"

    with pytest.raises(AppError) as oversized:
        await service.create("user-1", "OTHER", "x" * 301, {}, [], "command-2")
    assert oversized.value.code == "FEEDBACK_DESCRIPTION_INVALID"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "description",
    [
        "加我微信 abc_123456",
        "详情访问 https://spam.example.com",
        "银行卡号 6222021234567890",
        "可以私下转账交易",
    ],
)
async def test_feedback_rejects_sensitive_contact_url_and_transaction_content(
    description: str,
) -> None:
    service = FeedbackService(CapturingFeedbackClient())

    with pytest.raises(AppError) as blocked:
        await service.create("user-1", "OTHER", description, {}, [], "command-1")

    assert blocked.value.code == "FEEDBACK_CONTENT_BLOCKED"


def test_feedback_upload_credential_is_short_lived_and_user_scoped() -> None:
    uploads = OssUploadService(
        endpoint="oss-cn-test.aliyuncs.com",
        bucket="private-bucket",
        access_key_id="access-key",
        access_key_secret="secret-key",
        clock=lambda: NOW,
    )

    credential = uploads.create_feedback_upload("user-1", "image/png")

    assert credential["key"].startswith("feedback/user-1/")
    assert credential["expires_at"] > NOW
    assert credential["max_bytes"] == 5 * 1024 * 1024
    assert credential["signature"]

    with pytest.raises(AppError) as unsafe_user:
        uploads.create_feedback_upload("../foreign-user", "image/png")
    assert unsafe_user.value.code == "FEEDBACK_UPLOAD_USER_INVALID"
