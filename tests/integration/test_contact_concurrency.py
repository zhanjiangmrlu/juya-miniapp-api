import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from juya_miniapp_api.infrastructure.db.session import create_engine, create_session_factory
from juya_miniapp_api.infrastructure.security.field_cipher import FieldCipher
from juya_miniapp_api.modules.contacts.repository import SQLAlchemyContactRepository
from juya_miniapp_api.modules.contacts.service import ContactService
from juya_miniapp_api.shared.errors import AppError
from juya_miniapp_api.shared.ids import new_ulid

NOW = datetime(2026, 9, 28, 19, 0, tzinfo=UTC)


def _database_url() -> str:
    url = os.environ.get("JUYA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("JUYA_TEST_DATABASE_URL is required for MySQL integration tests")
    return url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)


@pytest.mark.asyncio
async def test_concurrent_real_changes_consume_only_one_opportunity() -> None:
    engine = create_engine(_database_url())
    factory = create_session_factory(engine)
    public_id = new_ulid(NOW)
    juya_number = f"JY{public_id[-12:]}"
    try:
        async with factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO user_account (public_id, juya_number, status) "
                    "VALUES (:public_id, :juya_number, 'ACTIVE')"
                ),
                {"public_id": public_id, "juya_number": juya_number},
            )
            user_id = await session.scalar(text("SELECT LAST_INSERT_ID()"))
            await session.execute(
                text("INSERT INTO user_profile (user_id, source) VALUES (:user_id, 'WECHAT')"),
                {"user_id": user_id},
            )

        service = ContactService(
            SQLAlchemyContactRepository(factory),
            FieldCipher(b"k" * 32, b"h" * 32),
        )
        await service.save(public_id, "initial-id", "privacy-v1", "PROFILE", NOW)

        results = await asyncio.gather(
            *(
                service.save(
                    public_id,
                    f"changed-{index:02d}",
                    "privacy-v1",
                    "PROFILE",
                    NOW + timedelta(seconds=index + 1),
                )
                for index in range(20)
            ),
            return_exceptions=True,
        )

        successes = [item for item in results if not isinstance(item, BaseException)]
        failures = [item for item in results if isinstance(item, AppError)]
        assert len(successes) == 1
        assert len(failures) == 19
        assert {item.code for item in failures} == {"CONTACT_SELF_EDIT_LIMIT"}
        current = await service.get(public_id)
        assert current is not None
        assert current.self_edit_count == 1
        assert current.change_pending is True
        async with factory() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT c.wechat_id_ciphertext, c.wechat_id_hmac, c.self_edit_count "
                            "FROM user_contact c JOIN user_account u ON u.id = c.user_id "
                            "WHERE u.public_id = :public_id"
                        ),
                        {"public_id": public_id},
                    )
                )
                .mappings()
                .one()
            )
            notes = (
                (
                    await session.execute(
                        text(
                            "SELECT h.note FROM contact_status_history h "
                            "JOIN user_account u ON u.id = h.user_id "
                            "WHERE u.public_id = :public_id"
                        ),
                        {"public_id": public_id},
                    )
                )
                .scalars()
                .all()
            )
        assert row["self_edit_count"] == 1
        assert row["wechat_id_hmac"] is not None
        assert b"changed" not in row["wechat_id_ciphertext"]
        assert all("initial-id" not in (note or "") for note in notes)
        assert all("changed" not in (note or "") for note in notes)
    finally:
        async with factory() as session, session.begin():
            await session.execute(
                text("DELETE FROM user_account WHERE public_id = :public_id"),
                {"public_id": public_id},
            )
        await engine.dispose()
