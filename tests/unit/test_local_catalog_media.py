import pytest
from httpx import ASGITransport, AsyncClient

from juya_miniapp_api.infrastructure.config import Settings
from juya_miniapp_api.main import create_app


@pytest.mark.asyncio
async def test_local_catalog_images_are_served_by_api_without_bundled_mock_media() -> None:
    app = create_app(Settings(environment="local", local_dev_mode=True))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        catalog = (await client.get("/api/v1/learning/catalog")).json()
        image = catalog["items"][0]["image_url"]
        assert image.startswith("http://test/local-dev/")
        assert (await client.get(image)).status_code == 200
