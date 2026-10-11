"""Actual admin runtime; only external OSS HEAD uses isolated fixture metadata."""

import os

from juya_admin_api.integrations.oss.aliyun import AliyunOssProvider
from juya_admin_api.integrations.oss.provider import ObjectMetadata


async def fixture_head(self: AliyunOssProvider, object_key: str) -> ObjectMetadata:
    assert os.environ.get("JUYA_V13_ISOLATED_DATABASE", "").startswith("juya_v13_")
    assert object_key.startswith("sealed/media/images/")
    return ObjectMetadata(object_key, 1, "image/png", "0" * 64, {})


AliyunOssProvider.head_object = fixture_head

from juya_admin_api.main import app  # noqa: E402, F401
