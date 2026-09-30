"""Run only with explicit test credentials; no production bucket calls."""

import base64
import os
from uuid import uuid4

import alibabacloud_oss_v2 as oss
import httpx
import pytest

from juya_miniapp_api.infrastructure.config import Settings
from juya_miniapp_api.integrations.oss.credentials import ControlledCredentialsProvider
from juya_miniapp_api.integrations.oss.upload import OssUploadService

pytestmark = pytest.mark.skipif(
    os.getenv("JUYA_RUN_LIVE_OSS_TESTS", "").lower() != "true",
    reason="live OSS tests are explicitly opt-in",
)


@pytest.mark.asyncio
async def test_v4_feedback_upload_uses_exact_key_mime_and_cleanup() -> None:
    settings = Settings()
    if (
        settings.environment != "test"
        or settings.oss_bucket != "juya-test"
        or settings.oss_expected_bucket != "juya-test"
    ):
        pytest.fail("Live tests require the explicitly bound test bucket", pytrace=False)
    settings.validate_oss_configuration()
    credentials = ControlledCredentialsProvider()
    service = OssUploadService(
        endpoint="https://oss-cn-shenzhen.aliyuncs.com",
        bucket="juya-test",
        region="cn-shenzhen",
        credentials_provider=credentials,
    )
    upload = service.create_feedback_upload("oss-live-" + uuid4().hex, "image/png")
    fields = upload["fields"]
    if not isinstance(fields, dict) or not isinstance(upload["key"], str):
        pytest.fail("Invalid upload contract", pytrace=False)
    config = oss.config.load_default()
    config.region = "cn-shenzhen"
    config.credentials_provider = credentials
    client = oss.Client(config)
    key = upload["key"]
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j6xkAAAAASUVORK5CYII="
    )
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            response = await http.post(
                str(upload["host"]), data=fields, files={"file": ("pixel.png", png, "image/png")}
            )
        if response.status_code != 200:
            pytest.fail(f"Feedback V4 upload returned HTTP {response.status_code}", pytrace=False)
        # Do not allow raw SDK exceptions to expose a signed request in test output.
        try:
            metadata = client.head_object(oss.HeadObjectRequest(bucket="juya-test", key=key))
        except Exception:
            pytest.fail("Feedback object HEAD failed (SDK details suppressed)", pytrace=False)
        assert metadata.content_length == len(png)
        assert metadata.content_type == "image/png"
    finally:
        try:
            client.delete_object(oss.DeleteObjectRequest(bucket="juya-test", key=key))
        except Exception:
            pytest.fail(
                "Owned feedback object cleanup failed (SDK details suppressed)", pytrace=False
            )
