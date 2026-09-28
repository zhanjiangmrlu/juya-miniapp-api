import secrets
from datetime import datetime

_CROCKFORD32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode(value: int, length: int) -> str:
    chars = ["0"] * length
    for index in range(length - 1, -1, -1):
        chars[index] = _CROCKFORD32[value & 31]
        value >>= 5
    return "".join(chars)


def new_ulid(now: datetime) -> str:
    return _encode(int(now.timestamp() * 1000), 10) + _encode(secrets.randbits(80), 16)
