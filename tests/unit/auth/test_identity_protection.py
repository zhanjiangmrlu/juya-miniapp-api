import pytest
from cryptography.exceptions import InvalidTag

from juya_miniapp_api.infrastructure.security.field_cipher import FieldCipher


def test_aes_gcm_round_trip_and_tamper_rejection() -> None:
    # 功能:验证AES-GCM加解密可逆且篡改密文被拒绝
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    cipher = FieldCipher(b"k" * 32, b"h" * 32)
    encrypted = cipher.encrypt("openid-secret", context=b"wechat-openid")

    assert cipher.decrypt(encrypted, context=b"wechat-openid") == "openid-secret"
    tampered = encrypted[:-1] + bytes([encrypted[-1] ^ 1])
    with pytest.raises(InvalidTag):
        cipher.decrypt(tampered, context=b"wechat-openid")


def test_lookup_hmac_is_stable_and_not_plaintext() -> None:
    # 功能:验证检索HMAC稳定且不保存敏感明文
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    cipher = FieldCipher(b"k" * 32, b"h" * 32)

    first = cipher.lookup_hmac("openid-secret")
    second = cipher.lookup_hmac("openid-secret")

    assert first == second
    assert len(first) == 32
    assert first != b"openid-secret"
