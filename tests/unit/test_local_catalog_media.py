import pytest
from httpx import ASGITransport, AsyncClient

from juya_miniapp_api.infrastructure.config import Settings
from juya_miniapp_api.main import create_app


@pytest.mark.asyncio
async def test_local_catalog_images_are_served_by_api_without_bundled_mock_media() -> None:
    # 功能:验证本地目录图片由API提供且不依赖前端内置模拟媒体
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    app = create_app(Settings(environment="local", local_dev_mode=True))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        catalog = (await client.get("/api/v1/learning/catalog")).json()
        image = catalog["items"][0]["image_url"]
        assert image.startswith("http://test/local-dev/")
        assert (await client.get(image)).status_code == 200
