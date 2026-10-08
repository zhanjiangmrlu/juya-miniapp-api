import pytest

from juya_miniapp_api.modules.feedback.service import FeedbackService
from juya_miniapp_api.shared.errors import AppError


class FeedbackClient:
    def __init__(self) -> None:
        # 功能:初始化管理端反馈读写客户端的状态存储
        # 参数:
        #     self: 当前管理端反馈读写客户端实例
        # 返回:无返回值。
        self.images: list[str] = []
        self.current_images: list[str] = []

    async def get_feedback(self, _: str) -> dict[str, object]:
        # 功能:在测试中获取反馈记录及处理进度
        # 参数:
        #     self: 当前管理端反馈读写客户端实例
        #     _: 接口约定传入的用户或反馈标识,当前测试桩不依赖具体取值
        # 返回:反馈标识、问题说明、截图对象键和处理进度
        return {"user_id": "user", "screenshots": self.current_images}

    async def supplement_feedback(self, *args: str, screenshots: list[str]) -> dict[str, object]:
        # 功能:在测试中向管理端提交反馈补充文本与截图
        # 参数:
        #     self: 当前管理端反馈读写客户端实例
        #     screenshots: 当前用户拥有的反馈截图OSS对象键列表
        #     args: 反馈补充调用按接口顺序传入的位置参数
        # 返回:补充文本与截图后更新的反馈记录
        self.images = screenshots
        return {"id": args[0]}


@pytest.mark.asyncio
async def test_feedback_screenshot_supplement_checks_owner_and_total_limit() -> None:
    # 功能:验证反馈补充截图校验归属和累计数量上限
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
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
