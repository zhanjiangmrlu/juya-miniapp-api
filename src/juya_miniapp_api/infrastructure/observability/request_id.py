import re
import secrets
from contextvars import ContextVar
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"
TRACEPARENT_HEADER = "traceparent"
_MAX_REQUEST_ID_LENGTH = 128
_TRACEPARENT_PATTERN = re.compile(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-(?:0[01])$")
_current_traceparent: ContextVar[str | None] = ContextVar("traceparent", default=None)


def _request_id_from_header(value: str | None) -> str:
    # 功能:校验传入请求标识并在缺失或无效时生成新标识
    # 参数:
    #     value: 客户端传入的X-Request-Id头值
    # 返回:有效的传入请求标识或新生成的标识
    if value and len(value) <= _MAX_REQUEST_ID_LENGTH and value.isascii():
        return value
    return uuid4().hex


def _traceparent_from_header(value: str | None) -> str:
    # 功能:校验链路追踪头并在无效时生成新的追踪上下文
    # 参数:
    #     value: 客户端传入的W3C traceparent头值
    # 返回:有效的传入或新生成的链路追踪头
    if value and _TRACEPARENT_PATTERN.fullmatch(value):
        return value
    return f"00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01"


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # 功能:注入请求标识和链路上下文并透传响应头
        # 参数:
        #     self: 当前小程序的RequestIdMiddleware实例
        #     request: FastAPI请求对象
        #     call_next: 继续调用后续ASGI中间件和路由的回调
        # 返回:HTTP响应对象
        request_id = _request_id_from_header(request.headers.get(REQUEST_ID_HEADER))
        traceparent = _traceparent_from_header(request.headers.get(TRACEPARENT_HEADER))
        request.state.request_id = request_id
        request.state.traceparent = traceparent
        token = _current_traceparent.set(traceparent)
        try:
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = request_id
            response.headers[TRACEPARENT_HEADER] = traceparent
            return response
        finally:
            _current_traceparent.reset(token)


def get_request_id(request: Request) -> str:
    # 功能:读取请求上下文中的请求标识
    # 参数:
    #     request: FastAPI请求对象
    # 返回:当前请求的标识字符串
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else uuid4().hex


def get_traceparent() -> str | None:
    # 功能:读取当前异步上下文的链路追踪头
    # 参数:
    #     无形参。
    # 返回:当前链路追踪头;上下文未设置时返回None
    return _current_traceparent.get()
