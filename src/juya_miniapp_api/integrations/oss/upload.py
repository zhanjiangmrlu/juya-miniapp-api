import base64
import hashlib
import hmac
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from juya_miniapp_api.integrations.oss.credentials import ControlledCredentialsProvider
from juya_miniapp_api.shared.errors import AppError
from juya_miniapp_api.shared.ids import new_ulid


class OssUploadService:
    # 匿名函数: clock默认时钟在调用时读取当前UTC时间
    # 参数:
    #     无形参。
    # 返回: 带UTC时区的当前时间
    def __init__(
        self,
        *,
        endpoint: str,
        bucket: str,
        access_key_id: str | None = None,
        access_key_secret: str | None = None,
        region: str | None = None,
        security_token: str | None = None,
        credentials_expires_at: datetime | None = None,
        credentials_provider: Any = None,
        max_bytes: int = 5 * 1024 * 1024,
        ttl: timedelta = timedelta(minutes=5),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        # 功能:初始化用户图片直传凭证服务并保存所需依赖与配置
        # 参数:
        #     self: 当前用户图片直传凭证服务实例
        #     endpoint: OSS HTTPS服务端点
        #     bucket: OSS存储桶名称
        #     access_key_id: OSS访问凭证标识
        #     access_key_secret: OSS访问凭证签名密钥
        #     region: OSS存储桶所在地域
        #     security_token: OSS临时凭证附带的STS安全令牌
        #     credentials_expires_at: OSS临时访问凭证的绝对失效时间
        #     credentials_provider: 读取或轮换OSS访问凭证的提供器
        #     max_bytes: 头像或截图上传允许的最大字节数
        #     ttl: 上传策略或缓存允许存活的时间间隔
        #     clock: 提供当前时间的可替换时钟回调
        # 返回:无返回值。
        parts = urlsplit(endpoint if "://" in endpoint else "https://" + endpoint)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.path not in {"", "/"}
            or parts.query
            or parts.fragment
            or parts.username
        ):
            raise RuntimeError("OSS endpoint must be an HTTPS service endpoint")
        self._endpoint = parts.netloc
        self._region = region or parts.hostname.removeprefix("oss-").removesuffix(".aliyuncs.com")
        self._bucket = bucket
        self._credentials = credentials_provider or ControlledCredentialsProvider(
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
            security_token=security_token,
            expires_at=credentials_expires_at,
        )
        self._max_bytes = max_bytes
        self._ttl = ttl
        self._clock = clock

    def create_feedback_upload(self, user_id: str, content_type: str) -> dict[str, object]:
        # 功能:签发绑定当前用户的反馈截图上传凭证
        # 参数:
        #     self: 当前用户图片直传凭证服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     content_type: 待上传图片的MIME类型,仅接受受支持图片格式
        # 返回:精确绑定截图对象键和MIME的OSS V4上传表单与有效期
        return self._create_upload(user_id, content_type, "feedback")

    def create_avatar_upload(self, user_id: str, content_type: str) -> dict[str, object]:
        # 功能:签发绑定当前用户的头像图片上传凭证
        # 参数:
        #     self: 当前用户图片直传凭证服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     content_type: 待上传图片的MIME类型,仅接受受支持图片格式
        # 返回:精确绑定头像对象键和MIME的OSS V4上传表单与有效期
        return self._create_upload(user_id, content_type, "uploads/avatars")

    def _create_upload(self, user_id: str, content_type: str, namespace: str) -> dict[str, object]:
        # 功能:签发绑定用户对象键和图片类型的OSS V4上传策略
        # 参数:
        #     self: 当前用户图片直传凭证服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     content_type: 待上传图片的MIME类型,仅接受受支持图片格式
        #     namespace: 缓存业务分区或上传对象所属目录
        # 返回:OSS上传地址、对象键、V4签名表单、大小上限与过期时间
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
        credentials = self._credentials.get_credentials()
        ttl = int(self._ttl.total_seconds())
        if not 1 <= ttl <= 600 or self._max_bytes < 1:
            raise AppError("OSS_POLICY_INVALID", "OSS上传策略限制无效", 422)
        if credentials.expiration is not None:
            if credentials.expiration.tzinfo is None:
                raise AppError("OSS_CREDENTIALS_EXPIRED", "OSS临时凭据时间无效", 503)
            ttl = min(ttl, int((credentials.expiration - now).total_seconds()) - 30)
            if ttl <= 0:
                raise AppError("OSS_CREDENTIALS_EXPIRED", "OSS临时凭据过期", 503)
        expires_at = now + timedelta(seconds=ttl)
        date = now.strftime("%Y%m%d")
        credential = f"{credentials.access_key_id}/{date}/{self._region}/oss/aliyun_v4_request"
        object_key = f"{namespace}/{user_id}/{new_ulid(now)}.{extension}"
        policy: dict[str, Any] = {
            "expiration": expires_at.isoformat().replace("+00:00", "Z"),
            "conditions": [
                {"bucket": self._bucket},
                {"key": object_key},
                {"x-oss-forbid-overwrite": "true"},
                {"Content-Type": content_type.lower()},
                {"x-oss-signature-version": "OSS4-HMAC-SHA256"},
                {"x-oss-credential": credential},
                {"x-oss-date": now.strftime("%Y%m%dT%H%M%SZ")},
                ["content-length-range", 1, self._max_bytes],
            ],
        }
        if credentials.security_token:
            policy["conditions"].append({"x-oss-security-token": credentials.security_token})
        encoded_policy = base64.b64encode(
            json.dumps(policy, separators=(",", ":")).encode()
        ).decode()
        signing_key = f"aliyun_v4{credentials.access_key_secret}".encode()
        for value in (date, self._region, "oss", "aliyun_v4_request"):
            signing_key = hmac.new(signing_key, value.encode(), hashlib.sha256).digest()
        signature = hmac.new(signing_key, encoded_policy.encode(), hashlib.sha256).hexdigest()
        fields = {
            "key": object_key,
            "x-oss-forbid-overwrite": "true",
            "policy": encoded_policy,
            "Content-Type": content_type.lower(),
            "x-oss-signature-version": "OSS4-HMAC-SHA256",
            "x-oss-credential": credential,
            "x-oss-date": now.strftime("%Y%m%dT%H%M%SZ"),
            "x-oss-signature": signature,
            "success_action_status": "200",
        }
        if credentials.security_token:
            fields["x-oss-security-token"] = credentials.security_token
        return {
            "host": f"https://{self._bucket}.{self._endpoint}",
            "key": object_key,
            "policy": encoded_policy,
            "signature": signature,
            "access_key_id": credentials.access_key_id,
            "fields": fields,
            "content_type": content_type.lower(),
            "max_bytes": self._max_bytes,
            "expires_at": expires_at,
        }
