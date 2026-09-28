import logging

from juya_miniapp_api.infrastructure.observability.logging import (
    SensitiveDataFilter,
    redact_value,
)


def test_redaction_removes_sensitive_keys_and_signed_query_values() -> None:
    value = {
        "access_token": "secret-token",
        "openid": "openid-value",
        "wechat_id": "wx-private",
        "description": "private feedback body",
        "url": "https://oss.example/a.png?Signature=secret&Expires=10",
        "safe": "visible",
    }

    redacted = redact_value(value)

    assert redacted["safe"] == "visible"
    assert redacted["access_token"] == "[REDACTED]"
    assert redacted["openid"] == "[REDACTED]"
    assert redacted["wechat_id"] == "[REDACTED]"
    assert redacted["description"] == "[REDACTED]"
    assert "secret" not in str(redacted)


def test_logging_filter_redacts_message_arguments() -> None:
    record = logging.LogRecord(
        "test",
        logging.INFO,
        __file__,
        1,
        "payload=%s",
        ({"refresh_token": "secret", "event": "login"},),
        None,
    )

    assert SensitiveDataFilter().filter(record)
    assert "secret" not in record.getMessage()
    assert "login" in record.getMessage()
