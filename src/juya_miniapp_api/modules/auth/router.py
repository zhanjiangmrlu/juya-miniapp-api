from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field

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


def create_auth_router(
    service: SessionService,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/session", tags=["session"])

    @router.post("/wechat")
    async def login(payload: WechatLoginRequest, response: Response) -> dict[str, object]:
        tokens = await service.login_with_wechat(payload.code, payload.device, clock())
        response.headers["Cache-Control"] = "no-store"
        return _response(tokens)

    @router.post("/refresh")
    async def refresh(payload: RefreshRequest, response: Response) -> dict[str, object]:
        tokens = await service.refresh(payload.refresh_token, clock())
        response.headers["Cache-Control"] = "no-store"
        return _response(tokens)

    @router.post("/logout", status_code=204)
    async def logout(payload: LogoutRequest) -> None:
        await service.logout(payload.session_id, clock())

    return router
