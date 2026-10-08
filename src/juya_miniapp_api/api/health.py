from collections.abc import Awaitable, Callable, Mapping

from fastapi import APIRouter

from juya_miniapp_api.shared.errors import AppError

ReadinessProbe = Callable[[], Awaitable[Mapping[str, bool]]]


async def default_readiness_probe() -> Mapping[str, bool]:
    # 功能:提供未接入外部探针时的默认就绪检查
    # 参数:
    #     无形参。
    # 返回:依赖名称到就绪状态的映射
    return {"configuration": True}


def create_health_router(service_name: str, readiness_probe: ReadinessProbe) -> APIRouter:
    # 功能:创建并绑定健康检查路由与业务依赖
    # 参数:
    #     service_name: 健康检查或签名随机数隔离使用的服务名称
    #     readiness_probe: 应用就绪接口使用的异步依赖检查回调
    # 返回:包含业务端点的FastAPI路由器
    router = APIRouter(prefix="/health", tags=["health"])

    @router.get("/live")
    async def live() -> dict[str, str]:
        # 功能:返回进程存活状态与服务名称
        # 参数:
        #     无形参。
        # 返回:进程存活状态及当前服务名称
        return {"status": "ok", "service": service_name}

    @router.get("/ready")
    async def ready() -> dict[str, object]:
        # 功能:执行就绪探针并在依赖未就绪时返回服务不可用
        # 参数:
        #     无形参。
        # 返回:依赖名称到就绪状态的映射及服务状态字段
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
