import logging

from juya_miniapp_api.infrastructure.observability.logging import (
    SensitiveDataFilter,
    redact_value,
)


def test_redaction_removes_sensitive_keys_and_signed_query_values() -> None:
    # 功能:验证日志脱敏移除敏感键和签名查询值
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
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
    # 功能:验证日志过滤器遮蔽格式化参数中的敏感内容
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
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


def test_nested_sdk_security_token_spelling_is_redacted() -> None:
    # 功能:验证嵌套SDK安全令牌字段的不同拼写均被遮蔽
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    record = logging.LogRecord(
        "application",
        logging.INFO,
        __file__,
        1,
        "credentials=%s",
        ({"Credentials": {"SecurityToken": "synthetic-private-token"}},),
        None,
    )
    SensitiveDataFilter().filter(record)
    assert "synthetic-private-token" not in record.getMessage()
