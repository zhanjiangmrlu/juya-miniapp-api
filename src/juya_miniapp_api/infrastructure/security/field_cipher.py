import hashlib
import hmac
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class FieldCipher:
    def __init__(self, encryption_key: bytes, lookup_key: bytes) -> None:
        if len(encryption_key) not in {16, 24, 32}:
            raise ValueError("AES key must be 128, 192, or 256 bits")
        self._cipher = AESGCM(encryption_key)
        self._lookup_key = lookup_key

    def encrypt(self, plaintext: str, *, context: bytes) -> bytes:
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(nonce, plaintext.encode("utf-8"), context)
        return b"\x01" + nonce + ciphertext

    def decrypt(self, envelope: bytes, *, context: bytes) -> str:
        if len(envelope) < 30 or envelope[0] != 1:
            raise ValueError("Unsupported encrypted field envelope")
        nonce = envelope[1:13]
        return self._cipher.decrypt(nonce, envelope[13:], context).decode("utf-8")

    def lookup_hmac(self, plaintext: str) -> bytes:
        return hmac.new(
            self._lookup_key,
            plaintext.encode("utf-8"),
            hashlib.sha256,
        ).digest()
