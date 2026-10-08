from datetime import UTC, datetime, timedelta

import pytest

from juya_miniapp_api.integrations.admin_api.client import AdminApiUnavailable
from juya_miniapp_api.integrations.admin_api.schemas import SceneOpenResult, SignedMedia
from juya_miniapp_api.modules.learning.access_service import AccessService, InMemoryOpenHistory
from juya_miniapp_api.shared.errors import AppError

NOW = datetime(2026, 9, 28, 21, 0, tzinfo=UTC)


class FailingAdminClient:
    async def open_scene(
        self, user_id: str, scene_id: str, idempotency_key: str
    ) -> SceneOpenResult:
        # 功能:在测试中校验场景访问响应并仅在授权成功后记录打开历史
        # 参数:
        #     self: 当前场景学习的FailingAdminClient实例
        #     user_id: 当前操作所属用户的公开标识
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        # 返回:访问级别与已发布场景内容
        del user_id, scene_id, idempotency_key
        raise AdminApiUnavailable()

    async def get_signed_media(self, user_id: str, target_id: str) -> SignedMedia:
        # 功能:在测试中获取授权有效期内的媒体访问签名链接
        # 参数:
        #     self: 当前场景学习的FailingAdminClient实例
        #     user_id: 当前操作所属用户的公开标识
        #     target_id: 需要学习或签发媒体链接的目标公开标识
        # 返回:授权有效期内的媒体签名链接
        del user_id, target_id
        raise AdminApiUnavailable()


class ExpiredMediaClient(FailingAdminClient):
    async def get_signed_media(self, user_id: str, target_id: str) -> SignedMedia:
        # 功能:在测试中获取授权有效期内的媒体访问签名链接
        # 参数:
        #     self: 当前场景学习的ExpiredMediaClient实例
        #     user_id: 当前操作所属用户的公开标识
        #     target_id: 需要学习或签发媒体链接的目标公开标识
        # 返回:授权有效期内的媒体签名链接
        del user_id, target_id
        return SignedMedia(
            target_id="audio-1",
            url="https://oss.example/expired-signature",
            expires_at=NOW - timedelta(seconds=1),
        )


@pytest.mark.asyncio
async def test_admin_failure_returns_pending_without_scene_or_local_open_state() -> None:
    # 功能:验证管理端故障返回待定状态且不暴露内容或创建本地打开记录
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    history = InMemoryOpenHistory()
    service = AccessService(FailingAdminClient(), history)

    result = await service.open_scene("user-1", "scene-1", "open-1", NOW)

    assert result.authorization_pending is True
    assert result.scene is None
    assert result.access is None
    assert history.opened == []


@pytest.mark.asyncio
async def test_expired_signed_media_is_rejected_without_returning_url() -> None:
    # 功能:验证过期媒体签名被拒绝且不返回链接
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    service = AccessService(ExpiredMediaClient(), InMemoryOpenHistory())

    with pytest.raises(AppError) as error:
        await service.get_signed_media("user-1", "audio-1", NOW)

    assert error.value.code == "SIGNED_MEDIA_EXPIRED"
    assert "expired-signature" not in repr(error.value)
