import asyncio
import hmac
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from juya_miniapp_api.infrastructure.analytics_events import append_event
from juya_miniapp_api.modules.auth.domain import SessionRecord
from juya_miniapp_api.modules.users.models import UserSummary
from juya_miniapp_api.shared.errors import AppError
from juya_miniapp_api.shared.ids import new_ulid


class AuthRepository(Protocol):
    async def get_or_create_user(
        self,
        app_id: str,
        openid_ciphertext: bytes,
        openid_hmac: bytes,
        now: datetime,
    ) -> UserSummary:
        # 功能:通过微信身份查找账号并在不存在时安全创建
        # 参数:
        #     self: 当前用户微信身份与登录会话仓库实例
        #     app_id: 微信小程序应用标识
        #     openid_ciphertext: 加密后的微信openid,避免明文存储身份
        #     openid_hmac: 微信openid的不可逆检索摘要
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:用户公开标识、句芽编号与账号状态
        ...

    async def create_session(self, session: SessionRecord) -> None:
        # 功能:保存登录会话与刷新凭证摘要
        # 参数:
        #     self: 当前用户微信身份与登录会话仓库实例
        #     session: 登录会话与刷新凭证摘要
        # 返回:无返回值。
        ...

    async def rotate_refresh(
        self,
        session_id: str,
        provided_hash: bytes,
        replacement_hash: bytes,
        replacement_expires_at: datetime,
        now: datetime,
    ) -> SessionRecord:
        # 功能:原子轮换刷新摘要并识别失效或重放凭证
        # 参数:
        #     self: 当前用户微信身份与登录会话仓库实例
        #     session_id: 登录会话的公开标识
        #     provided_hash: 客户端提交刷新凭证的SHA256摘要
        #     replacement_hash: 新刷新凭证的SHA256摘要
        #     replacement_expires_at: 轮换后刷新凭证与会话的失效时间
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:登录会话与刷新凭证摘要
        ...

    async def revoke_session(self, session_id: str, now: datetime) -> None:
        # 功能:标记单个登录会话失效
        # 参数:
        #     self: 当前用户微信身份与登录会话仓库实例
        #     session_id: 登录会话的公开标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:无返回值。
        ...

    async def revoke_all(self, user_id: str, reason: str, now: datetime) -> None:
        # 功能:撤销用户全部会话并记录撤销原因
        # 参数:
        #     self: 当前用户微信身份与登录会话仓库实例
        #     user_id: 当前操作所属用户的公开标识
        #     reason: 撤销、纠错或反馈异议的业务原因说明
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:无返回值。
        ...

    async def validate_access_session(
        self, user_id: str, session_id: str, now: datetime
    ) -> None:
        # 功能:校验访问凭证对应会话仍有效且账号允许访问
        # 参数:
        #     self: 当前用户微信身份与登录会话仓库实例
        #     user_id: 当前操作所属用户的公开标识
        #     session_id: 登录会话的公开标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:无返回值。
        ...


class InMemoryAuthRepository:
    def __init__(self) -> None:
        # 功能:初始化登录会话的InMemoryAuthRepository对象的状态存储
        # 参数:
        #     self: 当前登录会话的InMemoryAuthRepository实例
        # 返回:无返回值。
        self.identities: dict[tuple[str, bytes], UserSummary] = {}
        self.sessions: dict[str, SessionRecord] = {}
        self.next_user_status = "ACTIVE"
        self._lock = asyncio.Lock()

    async def get_or_create_user(
        self,
        app_id: str,
        openid_ciphertext: bytes,
        openid_hmac: bytes,
        now: datetime,
    ) -> UserSummary:
        # 功能:通过微信身份查找账号并在不存在时安全创建
        # 参数:
        #     self: 当前登录会话的InMemoryAuthRepository实例
        #     app_id: 微信小程序应用标识
        #     openid_ciphertext: 加密后的微信openid,避免明文存储身份
        #     openid_hmac: 微信openid的不可逆检索摘要
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:用户公开标识、句芽编号与账号状态
        del openid_ciphertext
        async with self._lock:
            existing = self.identities.get((app_id, openid_hmac))
            if existing is not None:
                return existing
            public_id = new_ulid(now)
            user = UserSummary(
                public_id,
                f"JY{public_id[-12:]}",
                self.next_user_status,
            )
            self.identities[(app_id, openid_hmac)] = user
            return user

    async def create_session(self, session: SessionRecord) -> None:
        # 功能:保存登录会话与刷新凭证摘要
        # 参数:
        #     self: 当前登录会话的InMemoryAuthRepository实例
        #     session: 登录会话与刷新凭证摘要
        # 返回:无返回值。
        self.sessions[session.id] = session

    async def rotate_refresh(
        self,
        session_id: str,
        provided_hash: bytes,
        replacement_hash: bytes,
        replacement_expires_at: datetime,
        now: datetime,
    ) -> SessionRecord:
        # 功能:原子轮换刷新摘要并识别失效或重放凭证
        # 参数:
        #     self: 当前登录会话的InMemoryAuthRepository实例
        #     session_id: 登录会话的公开标识
        #     provided_hash: 客户端提交刷新凭证的SHA256摘要
        #     replacement_hash: 新刷新凭证的SHA256摘要
        #     replacement_expires_at: 轮换后刷新凭证与会话的失效时间
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:登录会话与刷新凭证摘要
        async with self._lock:
            session = self.sessions.get(session_id)
            if session is None:
                raise AppError("SESSION_INVALID", "会话无效", 401)
            if session.revoked_at is not None:
                raise AppError("SESSION_REVOKED", "会话已撤销", 401)
            if now >= session.expires_at:
                session.revoked_at = now
                raise AppError("SESSION_EXPIRED", "会话已过期", 401)
            if not hmac.compare_digest(session.refresh_token_hash, provided_hash):
                session.revoked_at = now
                raise AppError("REFRESH_TOKEN_REPLAYED", "刷新凭证已使用", 401)
            session.refresh_token_hash = replacement_hash
            session.expires_at = replacement_expires_at
            return session

    async def revoke_session(self, session_id: str, now: datetime) -> None:
        # 功能:标记单个登录会话失效
        # 参数:
        #     self: 当前登录会话的InMemoryAuthRepository实例
        #     session_id: 登录会话的公开标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:无返回值。
        session = self.sessions.get(session_id)
        if session is not None and session.revoked_at is None:
            session.revoked_at = now

    async def revoke_all(self, user_id: str, reason: str, now: datetime) -> None:
        # 功能:撤销用户全部会话并记录撤销原因
        # 参数:
        #     self: 当前登录会话的InMemoryAuthRepository实例
        #     user_id: 当前操作所属用户的公开标识
        #     reason: 撤销、纠错或反馈异议的业务原因说明
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:无返回值。
        del reason
        for session in self.sessions.values():
            if session.user.public_id == user_id and session.revoked_at is None:
                session.revoked_at = now

    async def validate_access_session(self, user_id: str, session_id: str, now: datetime) -> None:
        # 功能:校验访问凭证对应会话仍有效且账号允许访问
        # 参数:
        #     self: 当前登录会话的InMemoryAuthRepository实例
        #     user_id: 当前操作所属用户的公开标识
        #     session_id: 登录会话的公开标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:无返回值。
        session = self.sessions.get(session_id)
        if session is None or session.user.public_id != user_id:
            raise AppError("SESSION_INVALID", "会话无效", 401)
        if session.revoked_at is not None:
            raise AppError("SESSION_REVOKED", "会话已撤销", 401)
        if now >= session.expires_at:
            raise AppError("SESSION_EXPIRED", "会话已过期", 401)


def _database_datetime(value: datetime) -> datetime:
    # 功能:将时间转换为数据库保存的无时区UTC时间
    # 参数:
    #     value: 待转换时区的必填数据库或业务时间
    # 返回:转换后的无时区UTC时间
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _utc_datetime(value: datetime) -> datetime:
    # 功能:将必填数据库时间统一为带UTC时区的时间
    # 参数:
    #     value: 待转换时区的必填数据库或业务时间
    # 返回:转换后的带UTC时区时间
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class SQLAlchemyAuthRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        # 功能:初始化登录会话的SQLAlchemyAuthRepository对象并保存所需依赖与配置
        # 参数:
        #     self: 当前登录会话的SQLAlchemyAuthRepository实例
        #     session_factory: 创建数据库事务会话的异步工厂
        # 返回:无返回值。
        self._session_factory = session_factory

    async def get_or_create_user(
        self,
        app_id: str,
        openid_ciphertext: bytes,
        openid_hmac: bytes,
        now: datetime,
    ) -> UserSummary:
        # 功能:通过微信身份查找账号并在不存在时安全创建
        # 参数:
        #     self: 当前登录会话的SQLAlchemyAuthRepository实例
        #     app_id: 微信小程序应用标识
        #     openid_ciphertext: 加密后的微信openid,避免明文存储身份
        #     openid_hmac: 微信openid的不可逆检索摘要
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:用户公开标识、句芽编号与账号状态
        async with self._session_factory() as session:
            existing = await self._find_user(session, app_id, openid_hmac)
            if existing is not None:
                await self._touch_user(session, existing.public_id, now)
                await session.commit()
                return existing

        public_id = new_ulid(now)
        user = UserSummary(public_id, f"JY{public_id[-12:]}", "ACTIVE")
        try:
            async with self._session_factory() as session, session.begin():
                await session.execute(
                    text(
                        "INSERT INTO user_account "
                        "(public_id, juya_number, status, last_active_at) "
                        "VALUES (:public_id, :juya_number, 'ACTIVE', :now)"
                    ),
                    {
                        "public_id": user.public_id,
                        "juya_number": user.juya_number,
                        "now": _database_datetime(now),
                    },
                )
                user_id = await session.scalar(text("SELECT LAST_INSERT_ID()"))
                if user_id is None:
                    raise RuntimeError("MySQL did not return the inserted user id")
                await append_event(
                    session,
                    event_key=f"user-created:{user_id}",
                    event_type="USER_CREATED",
                    user_id=int(user_id),
                    occurred_at=now,
                )
                await session.execute(
                    text("INSERT INTO user_profile (user_id, source) VALUES (:user_id, 'WECHAT')"),
                    {"user_id": user_id},
                )
                await session.execute(
                    text(
                        "INSERT INTO user_app_identity "
                        "(user_id, app_id, openid_ciphertext, openid_hmac) "
                        "VALUES (:user_id, :app_id, :ciphertext, :openid_hmac)"
                    ),
                    {
                        "user_id": user_id,
                        "app_id": app_id,
                        "ciphertext": openid_ciphertext,
                        "openid_hmac": openid_hmac,
                    },
                )
            return user
        except IntegrityError as error:
            async with self._session_factory() as session:
                winner = await self._find_user(session, app_id, openid_hmac)
            if winner is None:
                raise error
            return winner

    async def create_session(self, session_record: SessionRecord) -> None:
        # 功能:保存登录会话与刷新凭证摘要
        # 参数:
        #     self: 当前登录会话的SQLAlchemyAuthRepository实例
        #     session_record: 待保存的登录会话及刷新摘要领域记录
        # 返回:无返回值。
        async with self._session_factory() as session, session.begin():
            user_id = await session.scalar(
                text("SELECT id FROM user_account WHERE public_id = :public_id"),
                {"public_id": session_record.user.public_id},
            )
            if user_id is None:
                raise AppError("USER_NOT_FOUND", "用户不存在", 404)
            await session.execute(
                text(
                    "INSERT INTO user_session "
                    "(id, user_id, refresh_token_hash, expires_at, device_digest) "
                    "VALUES (:id, :user_id, :refresh_hash, :expires_at, :device_digest)"
                ),
                {
                    "id": session_record.id,
                    "user_id": user_id,
                    "refresh_hash": session_record.refresh_token_hash,
                    "expires_at": _database_datetime(session_record.expires_at),
                    "device_digest": session_record.device_digest,
                },
            )

    async def rotate_refresh(
        self,
        session_id: str,
        provided_hash: bytes,
        replacement_hash: bytes,
        replacement_expires_at: datetime,
        now: datetime,
    ) -> SessionRecord:
        # 功能:原子轮换刷新摘要并识别失效或重放凭证
        # 参数:
        #     self: 当前登录会话的SQLAlchemyAuthRepository实例
        #     session_id: 登录会话的公开标识
        #     provided_hash: 客户端提交刷新凭证的SHA256摘要
        #     replacement_hash: 新刷新凭证的SHA256摘要
        #     replacement_expires_at: 轮换后刷新凭证与会话的失效时间
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:登录会话与刷新凭证摘要
        error: AppError | None = None
        result: SessionRecord | None = None
        async with self._session_factory() as session, session.begin():
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT s.id, s.refresh_token_hash, s.expires_at, s.revoked_at, "
                            "s.device_digest, u.public_id, u.juya_number, u.status "
                            "FROM user_session s JOIN user_account u ON u.id = s.user_id "
                            "WHERE s.id = :session_id FOR UPDATE"
                        ),
                        {"session_id": session_id},
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                error = AppError("SESSION_INVALID", "会话无效", 401)
            elif row["revoked_at"] is not None:
                error = AppError("SESSION_REVOKED", "会话已撤销", 401)
            elif now >= _utc_datetime(row["expires_at"]):
                await self._mark_revoked(session, session_id, now)
                error = AppError("SESSION_EXPIRED", "会话已过期", 401)
            elif not hmac.compare_digest(row["refresh_token_hash"], provided_hash):
                await self._mark_revoked(session, session_id, now)
                error = AppError("REFRESH_TOKEN_REPLAYED", "刷新凭证已使用", 401)
            else:
                await session.execute(
                    text(
                        "UPDATE user_session SET refresh_token_hash = :refresh_hash, "
                        "expires_at = :expires_at WHERE id = :session_id"
                    ),
                    {
                        "refresh_hash": replacement_hash,
                        "expires_at": _database_datetime(replacement_expires_at),
                        "session_id": session_id,
                    },
                )
                result = SessionRecord(
                    id=row["id"],
                    user=UserSummary(row["public_id"], row["juya_number"], row["status"]),
                    refresh_token_hash=replacement_hash,
                    expires_at=replacement_expires_at,
                    device_digest=row["device_digest"] or b"",
                    revoked_at=None,
                )
        if error is not None:
            raise error
        if result is None:
            raise RuntimeError("Refresh rotation completed without a result")
        return result

    async def revoke_session(self, session_id: str, now: datetime) -> None:
        # 功能:标记单个登录会话失效
        # 参数:
        #     self: 当前登录会话的SQLAlchemyAuthRepository实例
        #     session_id: 登录会话的公开标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:无返回值。
        async with self._session_factory() as session, session.begin():
            await self._mark_revoked(session, session_id, now)

    async def revoke_all(self, user_id: str, reason: str, now: datetime) -> None:
        # 功能:撤销用户全部会话并记录撤销原因
        # 参数:
        #     self: 当前登录会话的SQLAlchemyAuthRepository实例
        #     user_id: 当前操作所属用户的公开标识
        #     reason: 撤销、纠错或反馈异议的业务原因说明
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:无返回值。
        del reason
        async with self._session_factory() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE user_session s JOIN user_account u ON u.id = s.user_id "
                    "SET s.revoked_at = :now "
                    "WHERE u.public_id = :public_id AND s.revoked_at IS NULL"
                ),
                {"now": _database_datetime(now), "public_id": user_id},
            )

    async def validate_access_session(self, user_id: str, session_id: str, now: datetime) -> None:
        # 功能:校验访问凭证对应会话仍有效且账号允许访问
        # 参数:
        #     self: 当前登录会话的SQLAlchemyAuthRepository实例
        #     user_id: 当前操作所属用户的公开标识
        #     session_id: 登录会话的公开标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:无返回值。
        async with self._session_factory() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT s.expires_at, s.revoked_at FROM user_session s "
                            "JOIN user_account u ON u.id = s.user_id "
                            "WHERE s.id = :session_id AND u.public_id = :user_id"
                        ),
                        {"session_id": session_id, "user_id": user_id},
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise AppError("SESSION_INVALID", "会话无效", 401)
        if row["revoked_at"] is not None:
            raise AppError("SESSION_REVOKED", "会话已撤销", 401)
        if now >= _utc_datetime(row["expires_at"]):
            raise AppError("SESSION_EXPIRED", "会话已过期", 401)

    @staticmethod
    async def _find_user(
        session: AsyncSession, app_id: str, openid_hmac: bytes
    ) -> UserSummary | None:
        # 功能:通过微信应用与openid摘要查找已有账号
        # 参数:
        #     session: 异步数据库会话
        #     app_id: 微信小程序应用标识
        #     openid_hmac: 微信openid的不可逆检索摘要
        # 返回:用户公开标识、句芽编号与账号状态;不存在或无候选时返回None
        row = (
            (
                await session.execute(
                    text(
                        "SELECT u.public_id, u.juya_number, u.status "
                        "FROM user_app_identity i JOIN user_account u ON u.id = i.user_id "
                        "WHERE i.app_id = :app_id AND i.openid_hmac = :openid_hmac"
                    ),
                    {"app_id": app_id, "openid_hmac": openid_hmac},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return UserSummary(row["public_id"], row["juya_number"], row["status"])

    @staticmethod
    async def _touch_user(session: AsyncSession, public_id: str, now: datetime) -> None:
        # 功能:更新账号的最近活跃时间
        # 参数:
        #     session: 异步数据库会话
        #     public_id: 当前操作所属用户账号的公开标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:无返回值。
        await session.execute(
            text("UPDATE user_account SET last_active_at = :now WHERE public_id = :public_id"),
            {"now": _database_datetime(now), "public_id": public_id},
        )

    @staticmethod
    async def _mark_revoked(session: AsyncSession, session_id: str, now: datetime) -> None:
        # 功能:在当前事务内标记登录会话失效
        # 参数:
        #     session: 异步数据库会话
        #     session_id: 登录会话的公开标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:无返回值。
        await session.execute(
            text(
                "UPDATE user_session SET revoked_at = COALESCE(revoked_at, :now) "
                "WHERE id = :session_id"
            ),
            {"now": _database_datetime(now), "session_id": session_id},
        )
