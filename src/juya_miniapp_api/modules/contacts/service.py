import hashlib
import re
import unicodedata
from datetime import datetime

from juya_miniapp_api.infrastructure.security.field_cipher import FieldCipher
from juya_miniapp_api.modules.contacts.domain import (
    AdminCorrectionRecord,
    AdminCorrectionView,
    ContactRecord,
    ContactView,
    CorrectionRequest,
)
from juya_miniapp_api.modules.contacts.repository import ContactRepository
from juya_miniapp_api.shared.errors import AppError

CONTACT_CONTEXT = b"user-wechat-id"
_WECHAT_ID = re.compile(r"^[a-z][a-z0-9_-]{5,19}$")
_CONTACT_STATUSES = {
    "NOT_PROVIDED",
    "PENDING",
    "CONTACTED",
    "UNREACHABLE",
    "DO_NOT_CONTACT",
}
_CORRECTION_STATUSES = {"PENDING", "PROCESSING", "APPROVED", "REJECTED", "CANCELLED"}


def normalize_wechat_id(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


class ContactService:
    def __init__(self, repository: ContactRepository, field_cipher: FieldCipher) -> None:
        self._repository = repository
        self._field_cipher = field_cipher

    async def get(self, user_id: str) -> ContactView | None:
        record = await self._repository.get_contact(user_id)
        return self._view(record) if record is not None else None

    async def find_user_by_wechat_id(self, wechat_id: str) -> str | None:
        normalized = normalize_wechat_id(wechat_id)
        if not _WECHAT_ID.fullmatch(normalized):
            return None
        return await self._repository.find_user_by_hmac(self._field_cipher.lookup_hmac(normalized))

    async def save(
        self,
        user_id: str,
        wechat_id: str,
        consent_version: str,
        source: str,
        now: datetime,
    ) -> ContactView:
        normalized = normalize_wechat_id(wechat_id)
        if not _WECHAT_ID.fullmatch(normalized):
            raise AppError("WECHAT_ID_INVALID", "微信号格式不正确", 422)
        ciphertext = self._field_cipher.encrypt(normalized, context=CONTACT_CONTEXT)
        lookup_hmac = self._field_cipher.lookup_hmac(normalized)
        record = await self._repository.save_contact(
            user_id,
            ciphertext,
            lookup_hmac,
            consent_version,
            source,
            now,
        )
        return self._view(record)

    async def withdraw(self, user_id: str, now: datetime) -> ContactView:
        return self._view(await self._repository.withdraw(user_id, now))

    async def request_correction(
        self, user_id: str, reason: str, now: datetime
    ) -> CorrectionRequest:
        normalized_reason = reason.strip()
        if not 2 <= len(normalized_reason) <= 500:
            raise AppError("CORRECTION_REASON_INVALID", "更正原因长度不正确", 422)
        return await self._repository.create_correction(user_id, normalized_reason, now)

    async def list_corrections(
        self, status: str | None, page: int, page_size: int
    ) -> tuple[tuple[AdminCorrectionView, ...], int]:
        if status is not None and status not in _CORRECTION_STATUSES:
            raise AppError("CORRECTION_STATUS_INVALID", "更正申请状态无效", 422)
        if page < 1 or not 1 <= page_size <= 100:
            raise AppError("CORRECTION_PAGE_INVALID", "分页参数无效", 422)
        records, total = await self._repository.list_corrections(status, page, page_size)
        return tuple(self._admin_view(item) for item in records), total

    async def get_correction(self, correction_id: str) -> AdminCorrectionView:
        record = await self._repository.get_correction(correction_id)
        if record is None:
            raise AppError("CONTACT_CORRECTION_NOT_FOUND", "更正申请不存在", 404)
        return self._admin_view(record)

    async def decide_correction(
        self,
        correction_id: str,
        decision: str,
        actor_id: str,
        idempotency_key: str,
        now: datetime,
    ) -> CorrectionRequest:
        if decision not in {"APPROVED", "REJECTED"}:
            raise AppError("CORRECTION_DECISION_INVALID", "更正决定无效", 422)
        if not 1 <= len(idempotency_key) <= 128:
            raise AppError("IDEMPOTENCY_KEY_INVALID", "幂等键无效", 422)
        request_hash = hashlib.sha256(f"{correction_id}\0{decision}".encode()).hexdigest()
        return await self._repository.decide_correction(
            correction_id,
            decision,
            actor_id,
            idempotency_key,
            request_hash,
            now,
        )

    async def update_status(
        self, user_id: str, status: str, actor_id: str, now: datetime
    ) -> ContactView:
        if status not in _CONTACT_STATUSES:
            raise AppError("CONTACT_STATUS_INVALID", "联系状态无效", 422)
        return self._view(await self._repository.update_status(user_id, status, actor_id, now))

    async def verify_change(self, user_id: str, actor_id: str, now: datetime) -> ContactView:
        return self._view(await self._repository.verify_change(user_id, actor_id, now))

    def _view(self, record: ContactRecord) -> ContactView:
        wechat_id = (
            self._field_cipher.decrypt(record.wechat_id_ciphertext, context=CONTACT_CONTEXT)
            if record.wechat_id_ciphertext is not None
            else None
        )
        correction_required = record.contact_status == "CONTACTED" or bool(record.verified_at)
        return ContactView(
            user_id=record.user_id,
            wechat_id=wechat_id,
            self_edit_count=record.self_edit_count,
            change_pending=record.change_pending,
            contact_status=record.contact_status,
            can_self_edit=(
                wechat_id is None or (record.self_edit_count < 1 and not correction_required)
            ),
            requires_correction=wechat_id is not None and correction_required,
            consent_version=record.consent_version,
            consented_at=record.consented_at,
            withdrawn_at=record.withdrawn_at,
            verified_at=record.verified_at,
            verified_by=record.verified_by,
            updated_at=record.updated_at,
        )

    def _admin_view(self, record: AdminCorrectionRecord) -> AdminCorrectionView:
        wechat_id = (
            self._field_cipher.decrypt(record.wechat_id_ciphertext, context=CONTACT_CONTEXT)
            if record.wechat_id_ciphertext is not None
            else None
        )
        return AdminCorrectionView(
            id=record.id,
            user_id=record.user_id,
            juya_number=record.juya_number,
            nickname=record.nickname,
            wechat_id=wechat_id,
            reason=record.reason,
            status=record.status,
            created_at=record.created_at,
            processed_at=record.processed_at,
            timeline=record.timeline,
        )
