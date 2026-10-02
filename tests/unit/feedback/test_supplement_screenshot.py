import pytest

from juya_miniapp_api.modules.feedback.service import FeedbackService
from juya_miniapp_api.shared.errors import AppError


class FeedbackClient:
    def __init__(self) -> None:
        self.images: list[str] = []
        self.current_images: list[str] = []

    async def get_feedback(self, _: str) -> dict[str, object]:
        return {"user_id": "user", "screenshots": self.current_images}

    async def supplement_feedback(self, *args: str, screenshots: list[str]) -> dict[str, object]:
        self.images = screenshots
        return {"id": args[0]}


@pytest.mark.asyncio
async def test_feedback_screenshot_supplement_checks_owner_and_total_limit() -> None:
    client = FeedbackClient()
    service = FeedbackService(client)
    await service.supplement(
        "user", "ticket", "details", "command", screenshots=["feedback/user/image.png"]
    )
    assert client.images == ["feedback/user/image.png"]
    with pytest.raises(AppError) as foreign:
        await service.supplement(
            "user", "ticket", "details", "command", screenshots=["feedback/other/image.png"]
        )
    assert foreign.value.code == "FEEDBACK_SCREENSHOT_INVALID"
    client.current_images = ["feedback/user/image.png"]
    with pytest.raises(AppError) as limit:
        await service.supplement(
            "user", "ticket", "details", "command", screenshots=["feedback/user/second.png"]
        )
    assert limit.value.code == "FEEDBACK_SCREENSHOT_LIMIT"
