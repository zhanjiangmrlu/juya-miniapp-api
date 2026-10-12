"""Check real routes and dependencies before a test release stops old services."""

import asyncio
import json

import httpx

from juya_miniapp_api.infrastructure.config import Settings


async def main() -> None:
    settings = Settings()
    if settings.environment != "test" or settings.local_dev_mode:
        raise RuntimeError("Only the real test environment may be deployed")
    if not settings.application_configured():
        raise RuntimeError("Required miniapp application configuration is incomplete")
    from juya_miniapp_api.main import app

    paths = app.openapi()["paths"]
    if "/api/v1/session/wechat" not in paths or "/api/v1/me" not in paths:
        raise RuntimeError("Real miniapp business routes are missing")
    resources = app.state.runtime_resources
    try:
        checks = await resources.readiness()
        if not checks or not all(checks.values()):
            raise RuntimeError("Miniapp dependency readiness failed: " + json.dumps(checks))
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(settings.admin_api_base_url + "/health/ready")
            response.raise_for_status()
    finally:
        await resources.close()
    print("Miniapp configuration, real routes, MySQL schema, Redis and admin API are ready")


if __name__ == "__main__":
    asyncio.run(main())
