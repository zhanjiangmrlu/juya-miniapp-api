import hashlib
import hmac
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class FieldCipher:
    def __init__(self, encryption_key: bytes, lookup_key: bytes) -> None:
        # 功能:初始化敏感字段加解密服务并保存所需依赖与配置
        # 参数:
        #     self: 当前敏感字段加解密服务实例
        #     encryption_key: AES-GCM敏感字段加解密密钥
        #     lookup_key: 生成敏感字段检索HMAC的独立密钥
        # 返回:无返回值。
        if len(encryption_key) not in {16, 24, 32}:
            raise ValueError("AES key must be 128, 192, or 256 bits")
        self._cipher = AESGCM(encryption_key)
        self._lookup_key = lookup_key

    def encrypt(self, plaintext: str, *, context: bytes) -> bytes:
        # 功能:使用AES-GCM和字段上下文加密敏感文本
        # 参数:
        #     self: 当前敏感字段加解密服务实例
        #     plaintext: 待加密或计算检索摘要的敏感文本
        #     context: AES-GCM的字段用途关联数据,防止密文跨字段替换
        # 返回:包含随机数与认证标签的AES-GCM加密封装
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(nonce, plaintext.encode("utf-8"), context)
        return b"\x01" + nonce + ciphertext

    def decrypt(self, envelope: bytes, *, context: bytes) -> str:
        # 功能:校验AES-GCM加密封装并解密敏感字段
        # 参数:
        #     self: 当前敏感字段加解密服务实例
        #     envelope: 包含随机数、认证标签和密文的敏感字段加密封装
        #     context: AES-GCM的字段用途关联数据,防止密文跨字段替换
        # 返回:认证成功后解密得到的敏感字段明文
        if len(envelope) < 30 or envelope[0] != 1:
            raise ValueError("Unsupported encrypted field envelope")
        nonce = envelope[1:13]
        return self._cipher.decrypt(nonce, envelope[13:], context).decode("utf-8")

    def lookup_hmac(self, plaintext: str) -> bytes:
        # 功能:计算敏感字段的不可逆检索摘要
        # 参数:
        #     self: 当前敏感字段加解密服务实例
        #     plaintext: 待加密或计算检索摘要的敏感文本
        # 返回:敏感字段的HMAC检索摘要
        return hmac.new(
            self._lookup_key,
            plaintext.encode("utf-8"),
            hashlib.sha256,
        ).digest()
