from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from juya_miniapp_api.api.health import (
    ReadinessProbe,
    create_health_router,
)
from juya_miniapp_api.api.local_dev import create_local_dev_router
from juya_miniapp_api.api.runtime import install_application_routes
from juya_miniapp_api.infrastructure.config import Settings
from juya_miniapp_api.infrastructure.observability.logging import configure_logging
from juya_miniapp_api.infrastructure.observability.request_id import RequestIdMiddleware
from juya_miniapp_api.shared.errors import install_error_handlers


def create_app(
    settings: Settings | None = None,
    *,
    readiness_probe: ReadinessProbe | None = None,
) -> FastAPI:
    runtime_settings = settings or Settings()
    if runtime_settings.environment not in {"local", "test"}:
        runtime_settings.validate_oss_configuration()
        if not runtime_settings.application_configured():
            raise RuntimeError("Production application configuration is incomplete")
    configure_logging(runtime_settings.log_level)
    app = FastAPI(title="Juya Miniapp API", version="0.1.0")
    app.state.settings = runtime_settings
    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)

    resources = None
    local_dev_enabled = runtime_settings.local_dev_mode and runtime_settings.environment in {
        "local",
        "test",
    }
    if local_dev_enabled:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
        app.include_router(create_local_dev_router())
    elif runtime_settings.application_configured():
        resources = install_application_routes(app, runtime_settings)
        app.state.runtime_resources = resources
        app.router.add_event_handler("shutdown", resources.close)

    async def configuration_readiness() -> dict[str, bool]:
        return {
            "configuration": runtime_settings.application_configured()
            or runtime_settings.environment in {"local", "test"}
        }

    app.include_router(
        create_health_router(
            runtime_settings.service_name,
            readiness_probe
            or (resources.readiness if resources is not None else configuration_readiness),
        )
    )
    return app


app = create_app()
