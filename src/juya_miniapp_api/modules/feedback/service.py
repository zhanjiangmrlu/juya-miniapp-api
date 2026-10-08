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
    ) -> dict[str, Any]:
        # 功能:提交当前用户反馈及来源和截图对象键
        # 参数:
        #     self: 当前管理端反馈读写客户端实例
        #     user_id: 当前操作所属用户的公开标识
        #     category: 用户反馈的问题分类
        #     description: 用户提交的反馈问题说明
        #     source: 反馈或收藏产生的场景、词条与页面来源信息
        #     screenshots: 当前用户拥有的反馈截图OSS对象键列表
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        # 返回:新建反馈记录及其处理状态
        ...

    async def list_feedback(self, user_id: str) -> list[dict[str, Any]]:
        # 功能:列出当前用户提交的反馈
        # 参数:
        #     self: 当前管理端反馈读写客户端实例
        #     user_id: 当前操作所属用户的公开标识
        # 返回:当前用户反馈记录及处理状态列表
        ...

    async def get_feedback(self, feedback_id: str) -> dict[str, Any]:
        # 功能:获取反馈记录及处理进度
        # 参数:
        #     self: 当前管理端反馈读写客户端实例
        #     feedback_id: 用户反馈记录的公开标识
        # 返回:反馈标识、问题说明、截图对象键和处理进度
        ...

    async def supplement_feedback(
        self,
        feedback_id: str,
        user_id: str,
        text: str,
        idempotency_key: str,
        *,
        screenshots: list[str] | None = None,
    ) -> dict[str, Any]:
        # 功能:向管理端提交反馈补充文本与截图
        # 参数:
        #     self: 当前管理端反馈读写客户端实例
        #     feedback_id: 用户反馈记录的公开标识
        #     user_id: 当前操作所属用户的公开标识
        #     text: 用户补充或校验的反馈文本
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        #     screenshots: 当前用户拥有的反馈截图OSS对象键列表
        # 返回:补充文本与截图后更新的反馈记录
        ...

    async def resolve_feedback(
        self,
        feedback_id: str,
        user_id: str,
        action: str,
        reason: str | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        # 功能:将反馈处理结果确认或异议发送至管理端
        # 参数:
        #     self: 当前管理端反馈读写客户端实例
        #     feedback_id: 用户反馈记录的公开标识
        #     user_id: 当前操作所属用户的公开标识
        #     action: 用户对反馈处理结果的确认或异议动作
        #     reason: 撤销、纠错或反馈异议的业务原因说明
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        # 返回:用户确认或提出异议后更新的反馈记录
        ...


class FeedbackService:
    def __init__(self, client: FeedbackClient, *, require_review: bool = False) -> None:
        # 功能:初始化用户反馈校验与上游编排服务并保存所需依赖与配置
        # 参数:
        #     self: 当前用户反馈校验与上游编排服务实例
        #     client: 管理端反馈读写客户端
        #     require_review: 是否对反馈文本执行敏感内容审核
        # 返回:无返回值。
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
        # 功能:校验反馈内容与截图归属并提交管理端
        # 参数:
        #     self: 当前用户反馈校验与上游编排服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     category: 用户反馈的问题分类
        #     description: 用户提交的反馈问题说明
        #     source: 反馈或收藏产生的场景、词条与页面来源信息
        #     screenshots: 当前用户拥有的反馈截图OSS对象键列表
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        # 返回:经归属校验或更新后的反馈记录和处理状态
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
        # 功能:列出当前用户提交的反馈
        # 参数:
        #     self: 当前用户反馈校验与上游编排服务实例
        #     user_id: 当前操作所属用户的公开标识
        # 返回:当前用户反馈记录及处理状态列表
        return await self._client.list_feedback(user_id)

    async def detail(self, user_id: str, feedback_id: str) -> dict[str, Any]:
        # 功能:读取反馈详情并校验记录属于当前用户
        # 参数:
        #     self: 当前用户反馈校验与上游编排服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     feedback_id: 用户反馈记录的公开标识
        # 返回:经归属校验或更新后的反馈记录和处理状态
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
        # 功能:校验反馈归属与截图数量并补充反馈内容
        # 参数:
        #     self: 当前用户反馈校验与上游编排服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     feedback_id: 用户反馈记录的公开标识
        #     text: 用户补充或校验的反馈文本
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        #     screenshots: 当前用户拥有的反馈截图OSS对象键列表
        # 返回:补充说明与截图后更新的反馈记录
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
        # 功能:校验反馈归属并提交用户的处理结果确认或异议
        # 参数:
        #     self: 当前用户反馈校验与上游编排服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     feedback_id: 用户反馈记录的公开标识
        #     action: 用户对反馈处理结果的确认或异议动作
        #     reason: 撤销、纠错或反馈异议的业务原因说明
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        # 返回:经归属校验或更新后的反馈记录和处理状态
        item = await self._client.get_feedback(feedback_id)
        self._assert_owner(item, user_id)
        return await self._client.resolve_feedback(
            feedback_id, user_id, action, reason, idempotency_key
        )

    @staticmethod
    def _assert_owner(item: dict[str, Any], user_id: str) -> None:
        # 功能:校验反馈记录是否属于当前用户
        # 参数:
        #     item: 用户反馈待校验或转换的一条记录
        #     user_id: 当前操作所属用户的公开标识
        # 返回:无返回值。
        if item.get("user_id") != user_id:
            raise AppError("FEEDBACK_NOT_FOUND", "反馈不存在", 404)
