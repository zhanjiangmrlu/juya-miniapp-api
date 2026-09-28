from collections.abc import Awaitable, Callable, Mapping

from fastapi import APIRouter

from juya_miniapp_api.shared.errors import AppError

ReadinessProbe = Callable[[], Awaitable[Mapping[str, bool]]]


async def default_readiness_probe() -> Mapping[str, bool]:
    return {"configuration": True}


def create_health_router(service_name: str, readiness_probe: ReadinessProbe) -> APIRouter:
    router = APIRouter(prefix="/health", tags=["health"])

    @router.get("/live")
    async def live() -> dict[str, str]:
        return {"status": "ok", "service": service_name}

    @router.get("/ready")
    async def ready() -> dict[str, object]:
        checks = dict(await readiness_probe())
        if not checks or not all(checks.values()):
            raise AppError(
                "SERVICE_NOT_READY",
                "服务尚未就绪",
                503,
                {"checks": checks},
            )
        return {"status": "ready", "checks": checks}

    return router
