import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from juya_miniapp_api.infrastructure.observability.metrics import LOGIN_ATTEMPTS
from juya_miniapp_api.infrastructure.redis.rate_limit import RateLimiter, enforce_rate_limit
from juya_miniapp_api.modules.auth.domain import SessionTokens
from juya_miniapp_api.modules.auth.service import SessionService

REFRESH_COOKIE = "juya_refresh_token"


class WechatLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=256)
    device: str = Field(min_length=1, max_length=200)


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=28, max_length=512)


class LogoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=26, max_length=26)


def _response(tokens: SessionTokens) -> dict[str, object]:
    # 功能:组装对客户端返回的登录会话凭证响应
    # 参数:
    #     tokens: 已签发的登录会话与访问刷新凭证集合
    # 返回:用户公开资料、会话标识、访问刷新凭证及注销等待标记
    return {
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "refresh_expires_at": tokens.refresh_expires_at,
        "session_id": tokens.session_id,
        "user": {
            "public_id": tokens.user.public_id,
            "juya_number": tokens.user.juya_number,
            "status": tokens.user.status,
        },
        "account_summary": tokens.account_summary,
    }


# 匿名函数: clock默认时钟在调用时读取当前UTC时间
# 参数:
#     无形参。
# 返回: 带UTC时区的当前时间
def create_auth_router(
    service: SessionService,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    rate_limiter: RateLimiter | None = None,
) -> APIRouter:
    # 功能:创建并绑定登录会话路由与业务依赖
    # 参数:
    #     service: 微信登录与凭证轮换服务,承载登录会话业务操作
    #     clock: 提供当前时间的可替换时钟回调
    #     rate_limiter: 按主体和业务范围控制请求频率的限流服务
    # 返回:包含业务端点的FastAPI路由器
    router = APIRouter(prefix="/api/v1/session", tags=["session"])

    @router.post("/wechat")
    async def login(
        payload: WechatLoginRequest, request: Request, response: Response
    ) -> dict[str, object]:
        # 功能:接收微信登录请求并签发会话凭证
        # 参数:
        #     payload: 已校验的微信登录码与设备信息
        #     request: FastAPI请求对象
        #     response: HTTP响应对象
        # 返回:微信登录后的用户资料、会话标识与访问刷新凭证
        now = clock()
        subject = request.client.host if request.client is not None else payload.device
        await enforce_rate_limit(
            rate_limiter,
            "login",
            subject,
            limit=20,
            window=timedelta(minutes=1),
            now=now,
        )
        try:
            tokens = await service.login_with_wechat(payload.code, payload.device, now)
        except Exception:
            LOGIN_ATTEMPTS.labels(outcome="failure").inc()
            raise
        LOGIN_ATTEMPTS.labels(outcome="success").inc()
        response.headers["Cache-Control"] = "no-store"
        return _response(tokens)

    @router.post("/refresh")
    async def refresh(payload: RefreshRequest, response: Response) -> dict[str, object]:
        # 功能:轮换刷新凭证并在重放时撤销会话族
        # 参数:
        #     payload: 已校验的客户端刷新凭证
        #     response: HTTP响应对象
        # 返回:轮换后的会话标识与访问刷新凭证
        now = clock()
        subject = hashlib.sha256(payload.refresh_token.encode()).hexdigest()[:24]
        await enforce_rate_limit(
            rate_limiter,
            "refresh",
            subject,
            limit=10,
            window=timedelta(minutes=1),
            now=now,
        )
        tokens = await service.refresh(payload.refresh_token, now)
        response.headers["Cache-Control"] = "no-store"
        return _response(tokens)

    @router.post("/logout", status_code=204)
    async def logout(payload: LogoutRequest) -> None:
        # 功能:撤销当前登录会话
        # 参数:
        #     payload: 已校验的待退出的会话标识
        # 返回:无返回值。
        await service.logout(payload.session_id, clock())

    return router
