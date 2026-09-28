import pytest
from cryptography.exceptions import InvalidTag

from juya_miniapp_api.infrastructure.security.field_cipher import FieldCipher


def test_aes_gcm_round_trip_and_tamper_rejection() -> None:
    cipher = FieldCipher(b"k" * 32, b"h" * 32)
    encrypted = cipher.encrypt("openid-secret", context=b"wechat-openid")

    assert cipher.decrypt(encrypted, context=b"wechat-openid") == "openid-secret"
    tampered = encrypted[:-1] + bytes([encrypted[-1] ^ 1])
    with pytest.raises(InvalidTag):
        cipher.decrypt(tampered, context=b"wechat-openid")


def test_lookup_hmac_is_stable_and_not_plaintext() -> None:
    cipher = FieldCipher(b"k" * 32, b"h" * 32)

    first = cipher.lookup_hmac("openid-secret")
    second = cipher.lookup_hmac("openid-secret")

    assert first == second
    assert len(first) == 32
    assert first != b"openid-secret"
