import base64
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Annotated, Any, cast

import httpx
from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from juya_miniapp_api.api.internal.v1.accounts import create_internal_accounts_router
from juya_miniapp_api.api.internal.v1.messages import create_internal_messages_router
from juya_miniapp_api.api.internal.v1.users import create_internal_users_router
from juya_miniapp_api.infrastructure.config import Settings
from juya_miniapp_api.infrastructure.db.schema_version import check_minimum_schema_version
from juya_miniapp_api.infrastructure.db.session import create_engine, create_session_factory
from juya_miniapp_api.infrastructure.observability.metrics import (
    DB_POOL_CONNECTIONS,
    REGISTRY,
)
from juya_miniapp_api.infrastructure.redis.cache import JsonCache
from juya_miniapp_api.infrastructure.redis.rate_limit import RedisRateLimiter
from juya_miniapp_api.infrastructure.security.field_cipher import FieldCipher
from juya_miniapp_api.infrastructure.security.jwt_service import JwtService
from juya_miniapp_api.infrastructure.security.service_hmac import (
    RedisNonceStore,
    ServicePrincipal,
    verify_request_signature,
)
from juya_miniapp_api.integrations.admin_api.client import AdminApiClient
from juya_miniapp_api.integrations.oss.credentials import ControlledCredentialsProvider
from juya_miniapp_api.integrations.oss.upload import OssUploadService
from juya_miniapp_api.integrations.wechat.client import WechatAuthClient
from juya_miniapp_api.modules.accounts.repository import SQLAlchemyAccountRepository
from juya_miniapp_api.modules.accounts.router import create_accounts_router
from juya_miniapp_api.modules.accounts.service import AccountLifecycleService
from juya_miniapp_api.modules.auth.repository import SQLAlchemyAuthRepository
from juya_miniapp_api.modules.auth.router import create_auth_router
from juya_miniapp_api.modules.auth.service import SessionService
from juya_miniapp_api.modules.checkins.repository import SQLAlchemyCheckinRepository
from juya_miniapp_api.modules.checkins.router import create_checkins_router
from juya_miniapp_api.modules.contacts.repository import SQLAlchemyContactRepository
from juya_miniapp_api.modules.contacts.router import create_contacts_router
from juya_miniapp_api.modules.contacts.service import ContactService
from juya_miniapp_api.modules.favorites.repository import SQLAlchemyFavoriteRepository
from juya_miniapp_api.modules.favorites.router import create_favorites_router
from juya_miniapp_api.modules.favorites.service import FavoriteService
from juya_miniapp_api.modules.feedback.router import create_feedback_router
from juya_miniapp_api.modules.feedback.service import FeedbackService
from juya_miniapp_api.modules.learning.access_router import create_content_access_router
from juya_miniapp_api.modules.learning.access_service import AccessService, SQLAlchemyOpenHistory
from juya_miniapp_api.modules.learning.admin_projection import (
    SQLAlchemyLearningOverviewRepository,
)
from juya_miniapp_api.modules.learning.catalog_service import CatalogService
from juya_miniapp_api.modules.learning.repository import SQLAlchemyLearningRepository
from juya_miniapp_api.modules.learning.router import create_learning_router
from juya_miniapp_api.modules.learning.service import LearningService
from juya_miniapp_api.modules.messages.repository import SQLAlchemyMessageRepository
from juya_miniapp_api.modules.messages.router import create_messages_router
from juya_miniapp_api.modules.messages.service import MessageService
from juya_miniapp_api.modules.users.repository import SQLAlchemyUserRepository
from juya_miniapp_api.modules.users.router import create_users_router
from juya_miniapp_api.modules.users.service import UserService
from juya_miniapp_api.shared.errors import AppError


def _required(value: str | SecretStr | None, name: str) -> str:
    if value is None:
        raise RuntimeError(f"Missing required setting: {name}")
    resolved = value.get_secret_value() if isinstance(value, SecretStr) else value
    if not resolved:
        raise RuntimeError(f"Missing required setting: {name}")
    return resolved


class RuntimeResources:
    def __init__(
        self,
        engine: AsyncEngine,
        redis: Redis,
        clients: tuple[httpx.AsyncClient, ...],
        readiness: Callable[[], Awaitable[Mapping[str, bool]]],
    ) -> None:
        self.engine = engine
        self.redis = redis
        self.clients = clients
        self.readiness = readiness

    async def close(self) -> None:
        for client in self.clients:
            await client.aclose()
        await self.redis.aclose()
        await self.engine.dispose()


def install_application_routes(app: FastAPI, settings: Settings) -> RuntimeResources:
    settings.validate_oss_configuration()
    database_url = _required(settings.database_url, "database_url")
    redis_url = _required(settings.redis_url, "redis_url")
    engine = create_engine(database_url)
    sessions = create_session_factory(engine)
    redis = Redis.from_url(redis_url, decode_responses=True)
    rate_limiter = RedisRateLimiter(cast(Any, redis))
    cache = JsonCache(cast(Any, redis))
    wechat_http = httpx.AsyncClient()
    admin_http = httpx.AsyncClient(base_url=settings.admin_api_base_url)

    field_cipher = FieldCipher(
        base64.urlsafe_b64decode(
            _required(settings.field_encryption_key_base64, "field_encryption_key_base64")
        ),
        _required(settings.field_lookup_key, "field_lookup_key").encode(),
    )
    jwt = JwtService(
        _required(settings.jwt_secret, "jwt_secret"),
        kid=settings.jwt_key_id,
    )
    auth_repository = SQLAlchemyAuthRepository(sessions)
    session_service = SessionService(
        WechatAuthClient(
            wechat_http,
            app_id=_required(settings.wechat_app_id, "wechat_app_id"),
            app_secret=_required(settings.wechat_app_secret, "wechat_app_secret"),
        ),
        auth_repository,
        field_cipher,
        jwt,
    )

    async def current_user(
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise AppError("ACCESS_TOKEN_REQUIRED", "请先登录", 401)
        claims = jwt.decode_access_token(
            authorization.removeprefix("Bearer ").strip(),
            now=datetime.now(UTC),
        )
        user_id = claims.get("sub")
        session_id = claims.get("sid")
        if not isinstance(user_id, str) or not isinstance(session_id, str):
            raise AppError("ACCESS_TOKEN_INVALID", "登录凭证无效", 401)
        await auth_repository.validate_access_session(user_id, session_id, datetime.now(UTC))
        return user_id

    nonce_store = RedisNonceStore(cast(Any, redis))
    hmac_secret = SecretStr(_required(settings.internal_hmac_secret, "internal_hmac_secret"))

    async def current_service(request: Request) -> ServicePrincipal:
        return await verify_request_signature(request, hmac_secret, nonce_store, datetime.now(UTC))

    @app.get("/internal/metrics", include_in_schema=False)
    async def metrics(
        _principal: Annotated[ServicePrincipal, Depends(current_service)],
    ) -> Response:
        return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)

    admin = AdminApiClient(
        admin_http,
        secret=_required(settings.internal_hmac_secret, "internal_hmac_secret").encode(),
    )
    contact_service = ContactService(SQLAlchemyContactRepository(sessions), field_cipher)
    user_service = UserService(SQLAlchemyUserRepository(sessions), contact_service)
    learning_repository = SQLAlchemyLearningRepository(sessions)
    learning_overviews = SQLAlchemyLearningOverviewRepository(sessions)
    learning_service = LearningService(learning_repository)
    catalog_service = CatalogService(admin, cache)
    access_service = AccessService(admin, SQLAlchemyOpenHistory(learning_repository))
    favorite_repository = SQLAlchemyFavoriteRepository(sessions)
    favorite_service = FavoriteService(favorite_repository)
    message_service = MessageService(SQLAlchemyMessageRepository(sessions))
    feedback_service = FeedbackService(admin)
    uploads = OssUploadService(
        endpoint=settings.oss_endpoint or f"https://oss-{settings.oss_region}.aliyuncs.com",
        bucket=_required(settings.oss_bucket, "oss_bucket"),
        region=_required(settings.oss_region, "oss_region"),
        credentials_provider=ControlledCredentialsProvider(
            mode=settings.oss_credentials_mode,
            role_name=settings.oss_ram_role_name,
            access_key_id=settings.oss_access_key_id.get_secret_value()
            if settings.oss_access_key_id
            else None,
            access_key_secret=settings.oss_access_key_secret.get_secret_value()
            if settings.oss_access_key_secret
            else None,
            security_token=settings.oss_session_token.get_secret_value()
            if settings.oss_session_token
            else None,
            expires_at=settings.oss_credentials_expires_at,
        ),
    )
    account_service = AccountLifecycleService(
        SQLAlchemyAccountRepository(sessions), session_service.revoke_all
    )

    for router in (
        create_auth_router(session_service, rate_limiter=rate_limiter),
        create_users_router(user_service, user_dependency=current_user),
        create_contacts_router(
            contact_service,
            user_dependency=current_user,
            rate_limiter=rate_limiter,
        ),
        create_learning_router(learning_service, learning_repository, user_dependency=current_user),
        create_checkins_router(
            SQLAlchemyCheckinRepository(sessions),
            user_dependency=current_user,
            learning=learning_repository,
            catalog=catalog_service,
            messages=message_service,
            favorites=favorite_repository,
        ),
        create_content_access_router(catalog_service, access_service, user_dependency=current_user),
        create_favorites_router(
            favorite_service,
            favorite_repository,
            user_dependency=current_user,
            catalog=catalog_service,
        ),
        create_messages_router(message_service, user_dependency=current_user),
        create_feedback_router(
            feedback_service,
            uploads,
            user_dependency=current_user,
            rate_limiter=rate_limiter,
        ),
        create_accounts_router(account_service, user_dependency=current_user),
        create_internal_users_router(
            user_service,
            contact_service,
            learning_overviews,
            service_dependency=current_service,
        ),
        create_internal_messages_router(
            message_service,
            current_service=current_service,
        ),
        create_internal_accounts_router(
            account_service,
            current_service=current_service,
        ),
    ):
        app.include_router(router)

    async def readiness() -> Mapping[str, bool]:
        try:
            checks = dict(await check_minimum_schema_version(sessions, 1))
        except Exception:
            checks = {"mysql": False, "schema": False}
        try:
            checks["redis"] = bool(await cast(Awaitable[object], redis.ping()))
        except Exception:
            checks["redis"] = False
        for state, attribute in (("idle", "checkedin"), ("in_use", "checkedout")):
            reader = getattr(engine.pool, attribute, None)
            if callable(reader):
                DB_POOL_CONNECTIONS.labels(state=state).set(float(reader()))
        return checks

    return RuntimeResources(engine, redis, (wechat_http, admin_http), readiness)
