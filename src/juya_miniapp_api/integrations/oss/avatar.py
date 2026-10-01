import asyncio
import base64
import hashlib
import re
from io import BytesIO
from typing import Any

import alibabacloud_oss_v2 as oss  # type: ignore[import-untyped]
from PIL import Image, UnidentifiedImageError

from juya_miniapp_api.shared.errors import AppError


def owned_avatar_key(user_id: str, key: str) -> bool:
    return bool(
        re.fullmatch(
            rf"(?:uploads/avatars|avatars)/{re.escape(user_id)}/[A-Za-z0-9_-]+\.(?:jpg|png|webp)",
            key,
        )
    )


class AvatarStore:
    """Only verified image bytes are copied into the private, server-owned avatar directory."""

    def __init__(
        self, *, region: str, bucket: str, endpoint: str | None, credentials_provider: Any
    ) -> None:
        config = oss.config.load_default()
        config.region = region
        config.credentials_provider = credentials_provider
        if endpoint:
            config.endpoint = endpoint
        self.client = oss.Client(config)
        self.bucket = bucket

    async def confirm(self, user_id: str, key: str) -> str:
        if not owned_avatar_key(user_id, key):
            raise AppError("AVATAR_OBJECT_KEY_INVALID", "头像对象键无效", 422)
        return await asyncio.to_thread(self._confirm, user_id, key)

    def _confirm(self, user_id: str, key: str) -> str:
        try:
            result = self.client.get_object(oss.GetObjectRequest(bucket=self.bucket, key=key))
            try:
                if int(result.content_length or 0) > 5 * 1024 * 1024:
                    raise AppError("AVATAR_IMAGE_INVALID", "头像大小超限", 422)
                data = bytearray()
                for chunk in result.body.iter_bytes(chunk_size=65536):
                    data.extend(chunk)
                    if len(data) > 5 * 1024 * 1024:
                        raise AppError("AVATAR_IMAGE_INVALID", "头像大小超限", 422)
            finally:
                result.body.close()
            with Image.open(BytesIO(data)) as image:
                if image.format not in {"JPEG", "PNG", "WEBP"} or not (
                    0 < image.width <= 4096 and 0 < image.height <= 4096
                ):
                    raise AppError("AVATAR_IMAGE_INVALID", "头像格式或尺寸无效", 422)
                image.load()
                output = BytesIO()
                image.convert("RGB").save(output, "PNG")
            if key.startswith("avatars/"):
                return key
            normalized = output.getvalue()
            fixed = f"avatars/{user_id}/{hashlib.sha256(normalized).hexdigest()}.png"
            # The exact content hash allows safe retries without overwriting an existing object.
            try:
                self.client.put_object(
                    oss.PutObjectRequest(
                        bucket=self.bucket,
                        key=fixed,
                        body=normalized,
                        content_type="image/png",
                        acl="private",
                        forbid_overwrite="true",
                        content_md5=base64.b64encode(
                            hashlib.md5(normalized, usedforsecurity=False).digest()
                        ).decode(),
                    )
                )
            except Exception as error:
                for _ in range(5):
                    unwrap = getattr(error, "unwrap", None)
                    if not callable(unwrap):
                        break
                    error = unwrap()
                if getattr(error, "code", None) != "FileAlreadyExists":
                    raise
            return fixed
        except AppError:
            raise
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            raise AppError("AVATAR_IMAGE_INVALID", "头像不能解码为图片", 422) from None
        except Exception:
            raise AppError("AVATAR_STORAGE_UNAVAILABLE", "头像校验暂不可用", 503) from None
