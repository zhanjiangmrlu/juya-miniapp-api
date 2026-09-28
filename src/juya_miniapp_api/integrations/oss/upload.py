import base64
import hashlib
import hmac
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from juya_miniapp_api.shared.errors import AppError
from juya_miniapp_api.shared.ids import new_ulid


class OssUploadService:
    def __init__(
        self,
        *,
        endpoint: str,
        bucket: str,
        access_key_id: str,
        access_key_secret: str,
        max_bytes: int = 5 * 1024 * 1024,
        ttl: timedelta = timedelta(minutes=5),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._endpoint = endpoint.removesuffix("/")
        self._bucket = bucket
        self._access_key_id = access_key_id
        self._secret = access_key_secret.encode()
        self._max_bytes = max_bytes
        self._ttl = ttl
        self._clock = clock

    def create_feedback_upload(self, user_id: str, content_type: str) -> dict[str, object]:
        if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", user_id) is None:
            raise AppError("FEEDBACK_UPLOAD_USER_INVALID", "用户标识无效", 422)
        extensions = {
            "image/jpeg": "jpg",
            "image/png": "png",
            "image/webp": "webp",
        }
        extension = extensions.get(content_type.lower())
        if extension is None:
            raise AppError("FEEDBACK_UPLOAD_TYPE_INVALID", "仅支持JPG、PNG或WEBP截图", 422)
        now = self._clock()
        expires_at = now + self._ttl
        object_key = f"feedback/{user_id}/{new_ulid(now)}.{extension}"
        policy = {
            "expiration": expires_at.isoformat().replace("+00:00", "Z"),
            "conditions": [
                {"bucket": self._bucket},
                {"key": object_key},
                {"Content-Type": content_type.lower()},
                ["content-length-range", 1, self._max_bytes],
            ],
        }
        encoded_policy = base64.b64encode(
            json.dumps(policy, separators=(",", ":")).encode()
        ).decode()
        signature = base64.b64encode(
            hmac.new(self._secret, encoded_policy.encode(), hashlib.sha1).digest()
        ).decode()
        return {
            "host": f"https://{self._bucket}.{self._endpoint}",
            "key": object_key,
            "policy": encoded_policy,
            "signature": signature,
            "access_key_id": self._access_key_id,
            "content_type": content_type.lower(),
            "max_bytes": self._max_bytes,
            "expires_at": expires_at,
        }
