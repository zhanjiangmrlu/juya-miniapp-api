import asyncio
import hmac
from datetime import UTC, datetime
from typing import Protocol, cast

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from juya_miniapp_api.modules.contacts.domain import (
    ContactAuditEvent,
    ContactRecord,
    CorrectionRequest,
)
from juya_miniapp_api.shared.errors import AppError
from juya_miniapp_api.shared.ids import new_ulid


class ContactRepository(Protocol):
    async def get_contact(self, user_id: str) -> ContactRecord | None: ...

    async def find_user_by_hmac(self, lookup_hmac: bytes) -> str | None: ...

    async def save_contact(
        self,
        user_id: str,
        ciphertext: bytes,
        lookup_hmac: bytes,
        consent_version: str,
        source: str,
        now: datetime,
    ) -> ContactRecord: ...

    async def withdraw(self, user_id: str, now: datetime) -> ContactRecord: ...

    async def create_correction(
        self, user_id: str, reason: str, now: datetime
    ) -> CorrectionRequest: ...

    async def decide_correction(
        self,
        correction_id: str,
        decision: str,
        actor_id: str,
        now: datetime,
    ) -> CorrectionRequest: ...

    async def update_status(
        self, user_id: str, status: str, actor_id: str, now: datetime
    ) -> ContactRecord: ...

    async def verify_change(self, user_id: str, actor_id: str, now: datetime) -> ContactRecord: ...


class InMemoryContactRepository:
    def __init__(self) -> None:
        self.records: dict[str, ContactRecord] = {}
        self.corrections: dict[str, CorrectionRequest] = {}
        self.audit_events: list[ContactAuditEvent] = []
        self._lock = asyncio.Lock()

    async def get_contact(self, user_id: str) -> ContactRecord | None:
        return self.records.get(user_id)

    async def find_user_by_hmac(self, lookup_hmac: bytes) -> str | None:
        for user_id, record in self.records.items():
            if record.wechat_id_hmac is not None and hmac.compare_digest(
                record.wechat_id_hmac, lookup_hmac
            ):
                return user_id
        return None

    async def save_contact(
        self,
        user_id: str,
        ciphertext: bytes,
        lookup_hmac: bytes,
        consent_version: str,
        source: str,
        now: datetime,
    ) -> ContactRecord:
        async with self._lock:
            record = self.records.get(user_id)
            if record is None:
                record = ContactRecord(
                    user_id=user_id,
                    wechat_id_ciphertext=ciphertext,
                    wechat_id_hmac=lookup_hmac,
                    consent_version=consent_version,
                    consented_at=now,
                    source=source,
                    self_edit_count=0,
                    withdrawn_at=None,
                    change_pending=False,
                    contact_status="PENDING",
                    verified_at=None,
                    verified_by=None,
                    updated_at=now,
                )
                self.records[user_id] = record
                self._audit(user_id, "CONTACT_CREATED", "USER", user_id, now)
                return record
            same_value = record.wechat_id_hmac is not None and hmac.compare_digest(
                record.wechat_id_hmac, lookup_hmac
            )
            if same_value:
                record.consent_version = consent_version
                record.consented_at = now
                record.source = source
                record.withdrawn_at = None
                record.updated_at = now
                self._audit(user_id, "CONTACT_CONSENT_RECONFIRMED", "USER", user_id, now)
                return record
            if record.wechat_id_hmac is not None:
                if record.contact_status in {"CONTACTED", "VERIFIED"} or record.verified_at:
                    raise AppError(
                        "CONTACT_CORRECTION_REQUIRED",
                        "联系方式已核对 请提交更正申请",
                        409,
                    )
                if record.self_edit_count >= 1:
                    raise AppError("CONTACT_SELF_EDIT_LIMIT", "自助修改次数已用完", 409)
                record.self_edit_count += 1
                record.change_pending = True
            record.wechat_id_ciphertext = ciphertext
            record.wechat_id_hmac = lookup_hmac
            record.consent_version = consent_version
            record.consented_at = now
            record.source = source
            record.withdrawn_at = None
            record.contact_status = "PENDING"
            record.verified_at = None
            record.verified_by = None
            record.updated_at = now
            self._audit(user_id, "CONTACT_CHANGED", "USER", user_id, now)
            return record

    async def withdraw(self, user_id: str, now: datetime) -> ContactRecord:
        async with self._lock:
            record = self.records.get(user_id)
            if record is None:
                record = ContactRecord(
                    user_id=user_id,
                    wechat_id_ciphertext=None,
                    wechat_id_hmac=None,
                    consent_version=None,
                    consented_at=None,
                    source=None,
                    self_edit_count=0,
                    withdrawn_at=now,
                    change_pending=False,
                    contact_status="NOT_PROVIDED",
                    verified_at=None,
                    verified_by=None,
                    updated_at=now,
                )
                self.records[user_id] = record
            else:
                record.wechat_id_ciphertext = None
                record.wechat_id_hmac = None
                record.consent_version = None
                record.consented_at = None
                record.source = None
                record.withdrawn_at = now
                record.change_pending = False
                record.contact_status = "NOT_PROVIDED"
                record.verified_at = None
                record.verified_by = None
                record.updated_at = now
            self._audit(user_id, "CONTACT_WITHDRAWN", "USER", user_id, now)
            return record

    async def create_correction(
        self, user_id: str, reason: str, now: datetime
    ) -> CorrectionRequest:
        async with self._lock:
            if any(
                item.user_id == user_id and item.status in {"PENDING", "PROCESSING"}
                for item in self.corrections.values()
            ):
                raise AppError("CONTACT_CORRECTION_ACTIVE", "已有待处理的更正申请", 409)
            correction = CorrectionRequest(
                public_id=new_ulid(now),
                user_id=user_id,
                reason=reason,
                status="PENDING",
                created_at=now,
            )
            self.corrections[correction.public_id] = correction
            self._audit(user_id, "CONTACT_CORRECTION_CREATED", "USER", user_id, now)
            return correction

    async def decide_correction(
        self,
        correction_id: str,
        decision: str,
        actor_id: str,
        now: datetime,
    ) -> CorrectionRequest:
        async with self._lock:
            correction = self.corrections.get(correction_id)
            if correction is None:
                raise AppError("CONTACT_CORRECTION_NOT_FOUND", "更正申请不存在", 404)
            if correction.status not in {"PENDING", "PROCESSING"}:
                raise AppError("CONTACT_CORRECTION_DECIDED", "更正申请已处理", 409)
            correction.status = decision
            correction.processed_at = now
            if decision == "APPROVED":
                record = self.records[correction.user_id]
                record.self_edit_count = 0
                record.verified_at = None
                record.verified_by = None
                if record.contact_status in {"CONTACTED", "VERIFIED"}:
                    record.contact_status = "PENDING"
            self._audit(
                correction.user_id,
                f"CONTACT_CORRECTION_{decision}",
                "ADMIN",
                actor_id,
                now,
            )
            return correction

    async def update_status(
        self, user_id: str, status: str, actor_id: str, now: datetime
    ) -> ContactRecord:
        async with self._lock:
            record = self._required(user_id)
            record.contact_status = status
            if status == "VERIFIED":
                record.verified_at = now
                record.verified_by = actor_id
            record.updated_at = now
            self._audit(user_id, "CONTACT_STATUS_CHANGED", "ADMIN", actor_id, now)
            return record

    async def verify_change(self, user_id: str, actor_id: str, now: datetime) -> ContactRecord:
        async with self._lock:
            record = self._required(user_id)
            record.change_pending = False
            record.verified_at = now
            record.verified_by = actor_id
            record.updated_at = now
            self._audit(user_id, "CONTACT_CHANGE_VERIFIED", "ADMIN", actor_id, now)
            return record

    def _required(self, user_id: str) -> ContactRecord:
        record = self.records.get(user_id)
        if record is None:
            raise AppError("CONTACT_NOT_FOUND", "联系方式不存在", 404)
        return record

    def _audit(
        self,
        user_id: str,
        event_type: str,
        actor_type: str,
        actor_id: str,
        now: datetime,
    ) -> None:
        self.audit_events.append(ContactAuditEvent(user_id, event_type, actor_type, actor_id, now))


def _database_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class SQLAlchemyContactRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_contact(self, user_id: str) -> ContactRecord | None:
        async with self._session_factory() as session:
            return await self._load_contact(session, user_id)

    async def find_user_by_hmac(self, lookup_hmac: bytes) -> str | None:
        async with self._session_factory() as session:
            return cast(
                str | None,
                await session.scalar(
                    text(
                        "SELECT u.public_id FROM user_contact c "
                        "JOIN user_account u ON u.id = c.user_id "
                        "WHERE c.wechat_id_hmac = :lookup_hmac"
                    ),
                    {"lookup_hmac": lookup_hmac},
                ),
            )

    async def save_contact(
        self,
        user_id: str,
        ciphertext: bytes,
        lookup_hmac: bytes,
        consent_version: str,
        source: str,
        now: datetime,
    ) -> ContactRecord:
        async with self._session_factory() as session, session.begin():
            internal_id = await self._lock_user(session, user_id)
            record = await self._load_contact(session, user_id, for_update=True)
            if record is None:
                await session.execute(
                    text(
                        "INSERT INTO user_contact "
                        "(user_id, wechat_id_ciphertext, wechat_id_hmac, consent_version, "
                        "consented_at, source, contact_status, updated_at) "
                        "VALUES (:user_id, :ciphertext, :lookup_hmac, :consent_version, "
                        ":now, :source, 'PENDING', :now)"
                    ),
                    {
                        "user_id": internal_id,
                        "ciphertext": ciphertext,
                        "lookup_hmac": lookup_hmac,
                        "consent_version": consent_version,
                        "source": source,
                        "now": _database_datetime(now),
                    },
                )
                await self._history(
                    session, internal_id, "PENDING", "USER", user_id, now, "CONTACT_CREATED"
                )
                created = await self._load_contact(session, user_id)
                if created is None:
                    raise RuntimeError("Contact insert was not visible")
                return created

            same_value = record.wechat_id_hmac is not None and hmac.compare_digest(
                record.wechat_id_hmac, lookup_hmac
            )
            if same_value:
                await session.execute(
                    text(
                        "UPDATE user_contact SET consent_version = :consent_version, "
                        "consented_at = :now, source = :source, withdrawn_at = NULL, "
                        "updated_at = :now WHERE user_id = :user_id"
                    ),
                    {
                        "consent_version": consent_version,
                        "source": source,
                        "now": _database_datetime(now),
                        "user_id": internal_id,
                    },
                )
                await self._history(
                    session,
                    internal_id,
                    record.contact_status,
                    "USER",
                    user_id,
                    now,
                    "CONTACT_CONSENT_RECONFIRMED",
                )
            else:
                count = record.self_edit_count
                change_pending = record.change_pending
                if record.wechat_id_hmac is not None:
                    if record.contact_status in {"CONTACTED", "VERIFIED"} or record.verified_at:
                        raise AppError(
                            "CONTACT_CORRECTION_REQUIRED",
                            "联系方式已核对 请提交更正申请",
                            409,
                        )
                    if count >= 1:
                        raise AppError("CONTACT_SELF_EDIT_LIMIT", "自助修改次数已用完", 409)
                    count += 1
                    change_pending = True
                await session.execute(
                    text(
                        "UPDATE user_contact SET wechat_id_ciphertext = :ciphertext, "
                        "wechat_id_hmac = :lookup_hmac, consent_version = :consent_version, "
                        "consented_at = :now, source = :source, self_edit_count = :edit_count, "
                        "withdrawn_at = NULL, change_pending = :change_pending, "
                        "contact_status = 'PENDING', verified_at = NULL, verified_by = NULL, "
                        "updated_at = :now WHERE user_id = :user_id"
                    ),
                    {
                        "ciphertext": ciphertext,
                        "lookup_hmac": lookup_hmac,
                        "consent_version": consent_version,
                        "source": source,
                        "edit_count": count,
                        "change_pending": change_pending,
                        "now": _database_datetime(now),
                        "user_id": internal_id,
                    },
                )
                await self._history(
                    session, internal_id, "PENDING", "USER", user_id, now, "CONTACT_CHANGED"
                )
            updated = await self._load_contact(session, user_id)
            if updated is None:
                raise RuntimeError("Contact update was not visible")
            return updated

    async def withdraw(self, user_id: str, now: datetime) -> ContactRecord:
        async with self._session_factory() as session, session.begin():
            internal_id = await self._lock_user(session, user_id)
            record = await self._load_contact(session, user_id, for_update=True)
            if record is None:
                await session.execute(
                    text(
                        "INSERT INTO user_contact "
                        "(user_id, withdrawn_at, contact_status, updated_at) "
                        "VALUES (:user_id, :now, 'NOT_PROVIDED', :now)"
                    ),
                    {"user_id": internal_id, "now": _database_datetime(now)},
                )
            else:
                await session.execute(
                    text(
                        "UPDATE user_contact SET wechat_id_ciphertext = NULL, "
                        "wechat_id_hmac = NULL, consent_version = NULL, consented_at = NULL, "
                        "source = NULL, withdrawn_at = :now, change_pending = 0, "
                        "contact_status = 'NOT_PROVIDED', verified_at = NULL, "
                        "verified_by = NULL, updated_at = :now WHERE user_id = :user_id"
                    ),
                    {"user_id": internal_id, "now": _database_datetime(now)},
                )
            await self._history(
                session,
                internal_id,
                "NOT_PROVIDED",
                "USER",
                user_id,
                now,
                "CONTACT_WITHDRAWN",
            )
            withdrawn = await self._load_contact(session, user_id)
            if withdrawn is None:
                raise RuntimeError("Contact withdrawal was not visible")
            return withdrawn

    async def create_correction(
        self, user_id: str, reason: str, now: datetime
    ) -> CorrectionRequest:
        public_id = new_ulid(now)
        try:
            async with self._session_factory() as session, session.begin():
                internal_id = await self._lock_user(session, user_id)
                await session.execute(
                    text(
                        "INSERT INTO contact_correction_request "
                        "(public_id, user_id, reason, status, created_at) "
                        "VALUES (:public_id, :user_id, :reason, 'PENDING', :now)"
                    ),
                    {
                        "public_id": public_id,
                        "user_id": internal_id,
                        "reason": reason,
                        "now": _database_datetime(now),
                    },
                )
            return CorrectionRequest(public_id, user_id, reason, "PENDING", now)
        except IntegrityError as error:
            raise AppError("CONTACT_CORRECTION_ACTIVE", "已有待处理的更正申请", 409) from error

    async def decide_correction(
        self,
        correction_id: str,
        decision: str,
        actor_id: str,
        now: datetime,
    ) -> CorrectionRequest:
        async with self._session_factory() as session, session.begin():
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT r.user_id, r.reason, r.status, r.created_at, u.public_id "
                            "FROM contact_correction_request r "
                            "JOIN user_account u ON u.id = r.user_id "
                            "WHERE r.public_id = :public_id FOR UPDATE"
                        ),
                        {"public_id": correction_id},
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise AppError("CONTACT_CORRECTION_NOT_FOUND", "更正申请不存在", 404)
            if row["status"] not in {"PENDING", "PROCESSING"}:
                raise AppError("CONTACT_CORRECTION_DECIDED", "更正申请已处理", 409)
            await session.execute(
                text(
                    "UPDATE contact_correction_request SET status = :decision, "
                    "processed_at = :now WHERE public_id = :public_id"
                ),
                {
                    "decision": decision,
                    "now": _database_datetime(now),
                    "public_id": correction_id,
                },
            )
            if decision == "APPROVED":
                await session.execute(
                    text(
                        "UPDATE user_contact SET self_edit_count = 0, verified_at = NULL, "
                        "verified_by = NULL, contact_status = CASE "
                        "WHEN contact_status IN ('CONTACTED','VERIFIED') THEN 'PENDING' "
                        "ELSE contact_status END WHERE user_id = :user_id"
                    ),
                    {"user_id": row["user_id"]},
                )
            await self._history(
                session,
                row["user_id"],
                "PENDING",
                "ADMIN",
                actor_id,
                now,
                f"CONTACT_CORRECTION_{decision}",
            )
            return CorrectionRequest(
                correction_id,
                row["public_id"],
                row["reason"],
                decision,
                _utc_datetime(row["created_at"]),
                now,
            )

    async def update_status(
        self, user_id: str, status: str, actor_id: str, now: datetime
    ) -> ContactRecord:
        async with self._session_factory() as session, session.begin():
            internal_id = await self._lock_user(session, user_id)
            if await self._load_contact(session, user_id, for_update=True) is None:
                raise AppError("CONTACT_NOT_FOUND", "联系方式不存在", 404)
            verified = status == "VERIFIED"
            await session.execute(
                text(
                    "UPDATE user_contact SET contact_status = :status, "
                    "verified_at = CASE WHEN :verified = 1 THEN :now ELSE verified_at END, "
                    "verified_by = CASE WHEN :verified = 1 THEN :actor_id ELSE verified_by END, "
                    "updated_at = :now WHERE user_id = :user_id"
                ),
                {
                    "status": status,
                    "verified": verified,
                    "now": _database_datetime(now),
                    "actor_id": actor_id,
                    "user_id": internal_id,
                },
            )
            await self._history(
                session, internal_id, status, "ADMIN", actor_id, now, "CONTACT_STATUS_CHANGED"
            )
            updated = await self._load_contact(session, user_id)
            if updated is None:
                raise RuntimeError("Contact status update was not visible")
            return updated

    async def verify_change(self, user_id: str, actor_id: str, now: datetime) -> ContactRecord:
        async with self._session_factory() as session, session.begin():
            internal_id = await self._lock_user(session, user_id)
            record = await self._load_contact(session, user_id, for_update=True)
            if record is None:
                raise AppError("CONTACT_NOT_FOUND", "联系方式不存在", 404)
            await session.execute(
                text(
                    "UPDATE user_contact SET change_pending = 0, verified_at = :now, "
                    "verified_by = :actor_id, updated_at = :now WHERE user_id = :user_id"
                ),
                {
                    "now": _database_datetime(now),
                    "actor_id": actor_id,
                    "user_id": internal_id,
                },
            )
            await self._history(
                session,
                internal_id,
                record.contact_status,
                "ADMIN",
                actor_id,
                now,
                "CONTACT_CHANGE_VERIFIED",
            )
            updated = await self._load_contact(session, user_id)
            if updated is None:
                raise RuntimeError("Contact verification was not visible")
            return updated

    @staticmethod
    async def _lock_user(session: AsyncSession, public_id: str) -> int:
        internal_id = await session.scalar(
            text("SELECT id FROM user_account WHERE public_id = :public_id FOR UPDATE"),
            {"public_id": public_id},
        )
        if internal_id is None:
            raise AppError("USER_NOT_FOUND", "用户不存在", 404)
        return int(internal_id)

    @staticmethod
    async def _load_contact(
        session: AsyncSession, public_id: str, *, for_update: bool = False
    ) -> ContactRecord | None:
        suffix = " FOR UPDATE" if for_update else ""
        row = (
            (
                await session.execute(
                    text(
                        "SELECT u.public_id, c.wechat_id_ciphertext, c.wechat_id_hmac, "
                        "c.consent_version, c.consented_at, c.source, c.self_edit_count, "
                        "c.withdrawn_at, c.change_pending, c.contact_status, c.verified_at, "
                        "c.verified_by, c.updated_at FROM user_contact c "
                        "JOIN user_account u ON u.id = c.user_id "
                        "WHERE u.public_id = :public_id" + suffix
                    ),
                    {"public_id": public_id},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return ContactRecord(
            user_id=row["public_id"],
            wechat_id_ciphertext=row["wechat_id_ciphertext"],
            wechat_id_hmac=row["wechat_id_hmac"],
            consent_version=row["consent_version"],
            consented_at=_utc_datetime(row["consented_at"]),
            source=row["source"],
            self_edit_count=row["self_edit_count"],
            withdrawn_at=_utc_datetime(row["withdrawn_at"]),
            change_pending=bool(row["change_pending"]),
            contact_status=row["contact_status"],
            verified_at=_utc_datetime(row["verified_at"]),
            verified_by=row["verified_by"],
            updated_at=_utc_datetime(row["updated_at"]) or datetime.now(UTC),
        )

    @staticmethod
    async def _history(
        session: AsyncSession,
        user_id: int,
        status: str,
        actor_type: str,
        actor_id: str,
        now: datetime,
        note: str,
    ) -> None:
        await session.execute(
            text(
                "INSERT INTO contact_status_history "
                "(user_id, status, actor_type, actor_id, occurred_at, note) "
                "VALUES (:user_id, :status, :actor_type, :actor_id, :now, :note)"
            ),
            {
                "user_id": user_id,
                "status": status,
                "actor_type": actor_type,
                "actor_id": actor_id,
                "now": _database_datetime(now),
                "note": note,
            },
        )
