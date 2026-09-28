from datetime import date
from typing import cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class SQLAlchemyCheckinRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_days(self, user_id: str) -> list[date]:
        async with self._session_factory() as session:
            values = cast(
                list[date],
                (
                    await session.execute(
                        text(
                            "SELECT d.beijing_date FROM daily_checkin d "
                            "JOIN user_account u ON u.id = d.user_id "
                            "WHERE u.public_id = :public_id ORDER BY d.beijing_date"
                        ),
                        {"public_id": user_id},
                    )
                )
                .scalars()
                .all(),
            )
        return values
