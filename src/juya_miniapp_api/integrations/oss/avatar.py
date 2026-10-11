import asyncio
import base64
import hashlib
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any

import alibabacloud_oss_v2 as oss  # type: ignore[import-untyped]
from PIL import Image, UnidentifiedImageError

from juya_miniapp_api.shared.errors import AppError


def owned_avatar_key(user_id: str, key: str) -> bool:
    # 功能:校验头像对象键属于指定用户且路径格式合法
    # 参数:
    #     user_id: 当前操作所属用户的公开标识
    #     key: 待校验归属并固定的头像OSS对象键
    # 返回:头像对象键是否属于该用户且路径合法
    return bool(
        re.fullmatch(
            rf"(?:uploads/avatars|avatars)/{re.escape(user_id)}/[A-Za-z0-9_-]+\.(?:jpg|png|webp)",
            key,
        )
    )


class AvatarStore:
    """Only verified image bytes are copied into the private, server-owned avatar directory."""

    def __init__(
        self,
        *,
        region: str,
        bucket: str,
        endpoint: str | None,
        credentials_provider: Any,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        # 功能:初始化小程序的AvatarStore对象并保存所需依赖与配置
        # 参数:
        #     self: 当前小程序的AvatarStore实例
        #     region: OSS存储桶所在地域
        #     bucket: OSS存储桶名称
        #     endpoint: OSS HTTPS服务端点
        #     credentials_provider: 读取或轮换OSS访问凭证的提供器
        # 返回:无返回值。
        config = oss.config.load_default()
        config.region = region
        config.credentials_provider = credentials_provider
        if endpoint:
            config.endpoint = endpoint
        self.client = oss.Client(config)
        self.bucket = bucket
        self._credentials_provider = credentials_provider
        self._clock = clock

    async def sign_avatar_url(self, user_id: str, key: str) -> str:
        """给本人已确认的私有头像签发最多五分钟的GET地址, 不读取云端对象。"""
        if not key.startswith("avatars/") or not owned_avatar_key(user_id, key):
            raise AppError("AVATAR_OBJECT_KEY_INVALID", "头像对象键无效", 422)
        return await asyncio.to_thread(self._sign_avatar_url, key)

    def _sign_avatar_url(self, key: str) -> str:
        try:
            credentials = self._credentials_provider.get_credentials()
            expiry = getattr(credentials, "expiration", None)
            ttl = 300
            if getattr(credentials, "security_token", None) and expiry is None:
                raise AppError("OSS_CREDENTIALS_EXPIRED", "OSS临时凭据有效期缺失", 503)
            if expiry is not None:
                if expiry.tzinfo is None:
                    raise AppError("OSS_CREDENTIALS_EXPIRED", "OSS临时凭据时间无效", 503)
                ttl = min(ttl, int((expiry - self._clock()).total_seconds()) - 30)
            if ttl <= 0:
                raise AppError("OSS_CREDENTIALS_EXPIRED", "OSS临时凭据已过期", 503)
            result = self.client.presign(
                oss.GetObjectRequest(bucket=self.bucket, key=key),
                expires=timedelta(seconds=ttl),
            )
            return str(result.url)
        except AppError:
            raise
        except Exception:
            raise AppError("AVATAR_STORAGE_UNAVAILABLE", "头像签名暂不可用", 503) from None

    async def confirm(self, user_id: str, key: str) -> str:
        # 功能:核验已上传头像的归属与图片内容并固定头像地址
        # 参数:
        #     self: 当前小程序的AvatarStore实例
        #     user_id: 当前操作所属用户的公开标识
        #     key: 待校验归属并固定的头像OSS对象键
        # 返回:核验并固定后的头像OSS对象键
        if not owned_avatar_key(user_id, key):
            raise AppError("AVATAR_OBJECT_KEY_INVALID", "头像对象键无效", 422)
        return await asyncio.to_thread(self._confirm, user_id, key)

    def _confirm(self, user_id: str, key: str) -> str:
        # 功能:核验头像对象内容并生成固定的标准化头像对象
        # 参数:
        #     self: 当前小程序的AvatarStore实例
        #     user_id: 当前操作所属用户的公开标识
        #     key: 待校验归属并固定的头像OSS对象键
        # 返回:核验并固定后的头像OSS对象键
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
