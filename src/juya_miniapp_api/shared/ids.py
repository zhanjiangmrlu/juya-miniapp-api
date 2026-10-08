import secrets
from datetime import datetime

_CROCKFORD32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode(value: int, length: int) -> str:
    # 功能:按指定长度将整数编码为ULID的Crockford Base32字符
    # 参数:
    #     value: 待编码的时间戳或随机部分整数
    #     length: ULID编码输出的固定字符数量
    # 返回:指定长度的Crockford Base32编码
    chars = ["0"] * length
    for index in range(length - 1, -1, -1):
        chars[index] = _CROCKFORD32[value & 31]
        value >>= 5
    return "".join(chars)


def new_ulid(now: datetime) -> str:
    # 功能:生成带毫秒时间戳和随机部分的ULID公开标识
    # 参数:
    #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
    # 返回:长度为26的ULID公开标识
    return _encode(int(now.timestamp() * 1000), 10) + _encode(secrets.randbits(80), 16)
