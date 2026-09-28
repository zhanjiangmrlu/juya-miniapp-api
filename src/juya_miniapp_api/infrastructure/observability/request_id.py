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
    if value and len(value) <= _MAX_REQUEST_ID_LENGTH and value.isascii():
        return value
    return uuid4().hex


def _traceparent_from_header(value: str | None) -> str:
    if value and _TRACEPARENT_PATTERN.fullmatch(value):
        return value
    return f"00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01"


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
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
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else uuid4().hex


def get_traceparent() -> str | None:
    return _current_traceparent.get()
