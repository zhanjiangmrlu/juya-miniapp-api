import logging
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SENSITIVE_KEYS = {
    "access_token",
    "refresh_token",
    "authorization",
    "token",
    "openid",
    "openid_ciphertext",
    "wechat_id",
    "wechat_id_ciphertext",
    "description",
    "feedback_body",
    "body",
}
_SIGNED_QUERY_KEYS = {
    "signature",
    "x-oss-signature",
    "x-oss-credential",
    "ossaccesskeyid",
    "security-token",
}


def _redact_url(value: str) -> str:
    if "?" not in value:
        return value
    parts = urlsplit(value)
    query = [
        (key, "[REDACTED]" if key.casefold() in _SIGNED_QUERY_KEYS else item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def redact_value(value: Any, *, key: str | None = None) -> Any:
    if key is not None and key.casefold() in _SENSITIVE_KEYS:
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {
            str(item_key): redact_value(item, key=str(item_key)) for item_key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return _redact_url(value)
    return value


class SensitiveDataFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            record.args = redact_value(record.args)
        for key, value in tuple(record.__dict__.items()):
            if key not in {"msg", "args"}:
                record.__dict__[key] = redact_value(value, key=key)
        return True


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(SensitiveDataFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
