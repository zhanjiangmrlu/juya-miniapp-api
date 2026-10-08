import re

from juya_miniapp_api.shared.errors import AppError

_BLOCKED_PATTERNS = (
    re.compile(r"https?://|www\.", re.IGNORECASE),
    re.compile(r"(?:微信|加微|v信|vx|qq)[\s:\uFF1A号]*[a-z0-9_-]{4,}", re.IGNORECASE),
    re.compile(r"(?:银行卡|卡号|bank\s*card).*\d{12,19}", re.IGNORECASE),
    re.compile(r"(?:身份证|证件号).*\d{15,18}[0-9x]?", re.IGNORECASE),
    re.compile(r"转账|私下交易|线下交易|付款码|收款码|代付"),
    re.compile(r"色情|博彩|赌博|刷单|代开发票|辱骂"),
)


def ensure_safe_feedback(value: str) -> None:
    # 功能:校验反馈文本并拒绝敏感联系方式、链接和交易内容
    # 参数:
    #     value: 待检查敏感联系方式、链接和交易内容的反馈文本
    # 返回:无返回值。
    if any(pattern.search(value) for pattern in _BLOCKED_PATTERNS):
        raise AppError(
            "FEEDBACK_CONTENT_BLOCKED",
            "反馈内容包含不允许提交的信息",
            422,
        )
