from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from juya_miniapp_api.modules.users.models import UserProfile
from juya_miniapp_api.shared.errors import AppError


class UserRepository(Protocol):
    async def get_profile(self, public_id: str) -> UserProfile | None: ...

    async def update_profile(
        self,
        public_id: str,
        nickname: str | None,
        avatar_object_key: str | None,
        now: datetime,
    ) -> UserProfile: ...

    async def search_profiles(
        self, *, juya_number: str | None, nickname: str | None, limit: int
    ) -> list[UserProfile]: ...


class InMemoryUserRepository:
    def __init__(self) -> None:
        self.profiles: dict[str, UserProfile] = {}

    def add(
        self,
        public_id: str,
        juya_number: str,
        *,
        nickname: str | None = None,
        avatar_object_key: str | None = None,
        status: str = "ACTIVE",
    ) -> None:
        self.profiles[public_id] = UserProfile(
            public_id, juya_number, status, nickname, avatar_object_key
        )

    async def get_profile(self, public_id: str) -> UserProfile | None:
        return self.profiles.get(public_id)

    async def update_profile(
        self,
        public_id: str,
        nickname: str | None,
        avatar_object_key: str | None,
        now: datetime,
    ) -> UserProfile:
        del now
        current = self.profiles.get(public_id)
        if current is None:
            raise AppError("USER_NOT_FOUND", "用户不存在", 404)
        updated = UserProfile(
            current.public_id,
            current.juya_number,
            current.status,
            nickname,
            avatar_object_key,
            current.created_at,
            current.last_active_at,
        )
        self.profiles[public_id] = updated
        return updated

    async def search_profiles(
        self, *, juya_number: str | None, nickname: str | None, limit: int
    ) -> list[UserProfile]:
        profiles = list(self.profiles.values())
        if juya_number:
            profiles = [item for item in profiles if item.juya_number.startswith(juya_number)]
        if nickname:
            profiles = [item for item in profiles if nickname in (item.nickname or "")]
        return profiles[:limit]


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class SQLAlchemyUserRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_profile(self, public_id: str) -> UserProfile | None:
        async with self._session_factory() as session:
            return await self._load(session, public_id)

    async def update_profile(
        self,
        public_id: str,
        nickname: str | None,
        avatar_object_key: str | None,
        now: datetime,
    ) -> UserProfile:
        database_now = now.astimezone(UTC).replace(tzinfo=None) if now.tzinfo else now
        async with self._session_factory() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE user_profile p JOIN user_account u ON u.id = p.user_id "
                    "SET p.nickname = :nickname, p.avatar_object_key = :avatar, "
                    "p.updated_at = :now WHERE u.public_id = :public_id"
                ),
                {
                    "nickname": nickname,
                    "avatar": avatar_object_key,
                    "now": database_now,
                    "public_id": public_id,
                },
            )
            profile = await self._load(session, public_id)
            if profile is None:
                raise AppError("USER_NOT_FOUND", "用户不存在", 404)
            return profile

    async def search_profiles(
        self, *, juya_number: str | None, nickname: str | None, limit: int
    ) -> list[UserProfile]:
        conditions: list[str] = []
        parameters: dict[str, object] = {"limit": limit}
        if juya_number:
            conditions.append("u.juya_number LIKE :juya_number")
            parameters["juya_number"] = f"{juya_number}%"
        if nickname:
            conditions.append("p.nickname LIKE :nickname")
            parameters["nickname"] = f"%{nickname}%"
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT u.public_id, u.juya_number, u.status, u.created_at, "
                            "u.last_active_at, p.nickname, p.avatar_object_key "
                            "FROM user_account u JOIN user_profile p ON p.user_id = u.id"
                            + where
                            + " ORDER BY u.created_at DESC LIMIT :limit"
                        ),
                        parameters,
                    )
                )
                .mappings()
                .all()
            )
        return [self._from_row(row) for row in rows]

    @classmethod
    async def _load(cls, session: AsyncSession, public_id: str) -> UserProfile | None:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT u.public_id, u.juya_number, u.status, u.created_at, "
                        "u.last_active_at, p.nickname, p.avatar_object_key "
                        "FROM user_account u JOIN user_profile p ON p.user_id = u.id "
                        "WHERE u.public_id = :public_id"
                    ),
                    {"public_id": public_id},
                )
            )
            .mappings()
            .first()
        )
        return cls._from_row(row) if row is not None else None

    @staticmethod
    def _from_row(mapping: RowMapping) -> UserProfile:
        return UserProfile(
            mapping["public_id"],
            mapping["juya_number"],
            mapping["status"],
            mapping["nickname"],
            mapping["avatar_object_key"],
            _utc(mapping["created_at"]),
            _utc(mapping["last_active_at"]),
        )
