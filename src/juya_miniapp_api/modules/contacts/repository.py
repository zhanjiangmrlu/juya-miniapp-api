import asyncio
import hmac
import json
from datetime import UTC, datetime
from typing import Protocol, cast
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from juya_miniapp_api.modules.contacts.domain import (
    AdminCorrectionRecord,
    ContactAuditEvent,
    ContactRecord,
    ContactTimelineEvent,
    CorrectionRequest,
)
from juya_miniapp_api.modules.contacts.events import append_contact_event
from juya_miniapp_api.shared.errors import AppError
from juya_miniapp_api.shared.ids import new_ulid

_SAFE_CONTACT_EVENT_TYPES = frozenset(
    {
        "CONTACT_CREATED",
        "CONTACT_CONSENT_RECONFIRMED",
        "CONTACT_CHANGED",
        "CONTACT_WITHDRAWN",
        "CONTACT_CORRECTION_CREATED",
        "CONTACT_CORRECTION_APPROVED",
        "CONTACT_CORRECTION_REJECTED",
        "CONTACT_STATUS_CHANGED",
        "CONTACT_CHANGE_VERIFIED",
    }
)


class ContactRepository(Protocol):
    async def has_prompt_exposure(self, user_id: str) -> bool:
        # 功能:查询用户是否已展示过联系方式引导
        # 参数:
        #     self: 当前联系方式与纠错申请仓库实例
        #     user_id: 当前操作所属用户的公开标识
        # 返回:该用户是否已有联系方式引导曝光
        ...

    async def record_prompt_exposure(
        self,
        user_id: str,
        idempotency_key: str,
        now: datetime,
    ) -> bool:
        # 功能:幂等保存用户联系方式引导曝光来源与分组
        # 参数:
        #     self: 当前联系方式与纠错申请仓库实例
        #     user_id: 当前操作所属用户的公开标识
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:是否为用户首次成功登记引导曝光
        ...

    async def get_contact(self, user_id: str) -> ContactRecord | None:
        # 功能:读取当前用户联系方式状态
        # 参数:
        #     self: 当前联系方式与纠错申请仓库实例
        #     user_id: 当前操作所属用户的公开标识
        # 返回:加密联系方式与变更状态记录;不存在或无候选时返回None
        ...

    async def get_contacts(self, user_ids: tuple[str, ...]) -> tuple[ContactRecord, ...]:
        # 功能:批量读取用户联系方式记录并保持输入顺序
        # 参数:
        #     self: 当前联系方式与纠错申请仓库实例
        #     user_ids: 需要批量查询联系方式的用户公开标识序列
        # 返回:加密联系方式与变更状态记录集合,保持输入的用户顺序
        ...

    async def find_user_by_hmac(self, lookup_hmac: bytes) -> str | None:
        # 功能:按微信号检索摘要查找所属用户
        # 参数:
        #     self: 当前联系方式与纠错申请仓库实例
        #     lookup_hmac: 标准化微信号的不可逆检索摘要
        # 返回:微信联系方式所属用户公开标识;未找到时为None
        ...

    async def save_contact(
        self,
        user_id: str,
        ciphertext: bytes,
        lookup_hmac: bytes,
        consent_version: str,
        source: str,
        now: datetime,
    ) -> ContactRecord:
        # 功能:保存联系方式并限制真实变更次数与纠错状态
        # 参数:
        #     self: 当前联系方式与纠错申请仓库实例
        #     user_id: 当前操作所属用户的公开标识
        #     ciphertext: 待解密的敏感字段密文字节
        #     lookup_hmac: 标准化微信号的不可逆检索摘要
        #     consent_version: 用户保存联系方式时同意的隐私条款版本
        #     source: 用户保存微信联系方式的来源页面或入口
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:加密联系方式与变更状态记录
        ...

    async def withdraw(self, user_id: str, now: datetime) -> ContactRecord:
        # 功能:撤回联系方式并删除加密内容与检索摘要
        # 参数:
        #     self: 当前联系方式与纠错申请仓库实例
        #     user_id: 当前操作所属用户的公开标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:加密联系方式与变更状态记录
        ...

    async def create_correction(
        self, user_id: str, reason: str, now: datetime
    ) -> CorrectionRequest:
        # 功能:创建联系方式纠错申请并限制重复有效申请
        # 参数:
        #     self: 当前联系方式与纠错申请仓库实例
        #     user_id: 当前操作所属用户的公开标识
        #     reason: 撤销、纠错或反馈异议的业务原因说明
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:联系方式纠错申请
        ...

    async def list_corrections(
        self, status: str | None, page: int, page_size: int
    ) -> tuple[tuple[AdminCorrectionRecord, ...], int]:
        # 功能:分页筛选管理员可见的联系方式纠错申请
        # 参数:
        #     self: 当前联系方式与纠错申请仓库实例
        #     status: 纠错申请状态筛选条件; None表示不按状态筛选
        #     page: 管理端纠错申请列表的页码
        #     page_size: 管理端纠错申请每页最多返回的记录数
        # 返回:本页纠错申请记录集合及符合条件的总记录数量
        ...

    async def get_correction(self, correction_id: str) -> AdminCorrectionRecord | None:
        # 功能:读取联系方式纠错申请的管理端详情
        # 参数:
        #     self: 当前联系方式与纠错申请仓库实例
        #     correction_id: 联系方式纠错申请的公开标识
        # 返回:管理端纠错申请与用户关联记录;不存在或无候选时返回None
        ...

    async def decide_correction(
        self,
        correction_id: str,
        decision: str,
        actor_id: str,
        idempotency_key: str,
        request_hash: str,
        now: datetime,
    ) -> CorrectionRequest:
        # 功能:幂等处理管理员的联系方式纠错批准或拒绝决定
        # 参数:
        #     self: 当前联系方式与纠错申请仓库实例
        #     correction_id: 联系方式纠错申请的公开标识
        #     decision: 管理员对纠错申请作出的批准或拒绝决定
        #     actor_id: 本次变更操作者的标识
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        #     request_hash: 纠错决定请求内容摘要,用于识别幂等键冲突
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:联系方式纠错申请
        ...

    async def update_status(
        self, user_id: str, status: str, actor_id: str, now: datetime
    ) -> ContactRecord:
        # 功能:修改联系方式业务状态并记录管理员审计事件
        # 参数:
        #     self: 当前联系方式与纠错申请仓库实例
        #     user_id: 当前操作所属用户的公开标识
        #     status: 管理员指定的新联系方式业务状态
        #     actor_id: 本次变更操作者的标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:加密联系方式与变更状态记录
        ...

    async def verify_change(self, user_id: str, actor_id: str, now: datetime) -> ContactRecord:
        # 功能:记录管理员对联系方式真实变更的核验
        # 参数:
        #     self: 当前联系方式与纠错申请仓库实例
        #     user_id: 当前操作所属用户的公开标识
        #     actor_id: 本次变更操作者的标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:加密联系方式与变更状态记录
        ...


class InMemoryContactRepository:
    def __init__(self) -> None:
        # 功能:初始化联系方式的InMemoryContactRepository对象的状态存储
        # 参数:
        #     self: 当前联系方式的InMemoryContactRepository实例
        # 返回:无返回值。
        self.records: dict[str, ContactRecord] = {}
        self.prompt_exposures: set[tuple[str, str]] = set()
        self.corrections: dict[str, CorrectionRequest] = {}
        self.admin_identities: dict[str, tuple[str, str | None]] = {}
        self.audit_events: list[ContactAuditEvent] = []
        self.decision_idempotency: dict[tuple[str, str], tuple[str, str]] = {}
        self._lock = asyncio.Lock()

    async def record_prompt_exposure(
        self,
        user_id: str,
        idempotency_key: str,
        now: datetime,
    ) -> bool:
        # 功能:幂等保存用户联系方式引导曝光来源与分组
        # 参数:
        #     self: 当前联系方式的InMemoryContactRepository实例
        #     user_id: 当前操作所属用户的公开标识
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:是否为用户首次成功登记引导曝光
        del now
        async with self._lock:
            key = (user_id, idempotency_key)
            if await self.has_prompt_exposure(user_id):
                return False
            self.prompt_exposures.add(key)
            return True

    async def has_prompt_exposure(self, user_id: str) -> bool:
        # 功能:查询用户是否已展示过联系方式引导
        # 参数:
        #     self: 当前联系方式的InMemoryContactRepository实例
        #     user_id: 当前操作所属用户的公开标识
        # 返回:该用户是否已有联系方式引导曝光
        return any(owner == user_id for owner, _ in self.prompt_exposures)

    async def get_contact(self, user_id: str) -> ContactRecord | None:
        # 功能:读取当前用户联系方式状态
        # 参数:
        #     self: 当前联系方式的InMemoryContactRepository实例
        #     user_id: 当前操作所属用户的公开标识
        # 返回:加密联系方式与变更状态记录;不存在或无候选时返回None
        return self.records.get(user_id)

    async def get_contacts(self, user_ids: tuple[str, ...]) -> tuple[ContactRecord, ...]:
        # 功能:批量读取用户联系方式记录并保持输入顺序
        # 参数:
        #     self: 当前联系方式的InMemoryContactRepository实例
        #     user_ids: 需要批量查询联系方式的用户公开标识序列
        # 返回:加密联系方式与变更状态记录集合,保持输入的用户顺序
        return tuple(self.records[user_id] for user_id in user_ids if user_id in self.records)

    async def find_user_by_hmac(self, lookup_hmac: bytes) -> str | None:
        # 功能:按微信号检索摘要查找所属用户
        # 参数:
        #     self: 当前联系方式的InMemoryContactRepository实例
        #     lookup_hmac: 标准化微信号的不可逆检索摘要
        # 返回:微信联系方式所属用户公开标识;未找到时为None
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
        # 功能:保存联系方式并限制真实变更次数与纠错状态
        # 参数:
        #     self: 当前联系方式的InMemoryContactRepository实例
        #     user_id: 当前操作所属用户的公开标识
        #     ciphertext: 待解密的敏感字段密文字节
        #     lookup_hmac: 标准化微信号的不可逆检索摘要
        #     consent_version: 用户保存联系方式时同意的隐私条款版本
        #     source: 用户保存微信联系方式的来源页面或入口
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:加密联系方式与变更状态记录
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
                if record.contact_status == "CONTACTED" or record.verified_at:
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
        # 功能:撤回联系方式并删除加密内容与检索摘要
        # 参数:
        #     self: 当前联系方式的InMemoryContactRepository实例
        #     user_id: 当前操作所属用户的公开标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:加密联系方式与变更状态记录
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
        # 功能:创建联系方式纠错申请并限制重复有效申请
        # 参数:
        #     self: 当前联系方式的InMemoryContactRepository实例
        #     user_id: 当前操作所属用户的公开标识
        #     reason: 撤销、纠错或反馈异议的业务原因说明
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:联系方式纠错申请
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

    async def list_corrections(
        self, status: str | None, page: int, page_size: int
    ) -> tuple[tuple[AdminCorrectionRecord, ...], int]:
        # 功能:分页筛选管理员可见的联系方式纠错申请
        # 参数:
        #     self: 当前联系方式的InMemoryContactRepository实例
        #     status: 纠错申请状态筛选条件; None表示不按状态筛选
        #     page: 管理端纠错申请列表的页码
        #     page_size: 管理端纠错申请每页最多返回的记录数
        # 返回:本页纠错申请记录集合及符合条件的总记录数量
        corrections = [
            item for item in self.corrections.values() if status is None or item.status == status
        ]
        # 匿名函数: key按创建时间与公开标识确定纠错申请的倒序排列
        # 参数:
        #     item: 当前待排序的联系方式纠错申请记录
        # 返回: 创建时间和公开标识组成的排序元组; 时间为空时采用UTC最小时间
        corrections.sort(
            key=lambda item: (item.created_at or datetime.min.replace(tzinfo=UTC), item.public_id),
            reverse=True,
        )
        start = (page - 1) * page_size
        records = tuple(
            self._admin_correction(item) for item in corrections[start : start + page_size]
        )
        return records, len(corrections)

    async def get_correction(self, correction_id: str) -> AdminCorrectionRecord | None:
        # 功能:读取联系方式纠错申请的管理端详情
        # 参数:
        #     self: 当前联系方式的InMemoryContactRepository实例
        #     correction_id: 联系方式纠错申请的公开标识
        # 返回:管理端纠错申请与用户关联记录;不存在或无候选时返回None
        correction = self.corrections.get(correction_id)
        return self._admin_correction(correction) if correction is not None else None

    async def decide_correction(
        self,
        correction_id: str,
        decision: str,
        actor_id: str,
        idempotency_key: str,
        request_hash: str,
        now: datetime,
    ) -> CorrectionRequest:
        # 功能:幂等处理管理员的联系方式纠错批准或拒绝决定
        # 参数:
        #     self: 当前联系方式的InMemoryContactRepository实例
        #     correction_id: 联系方式纠错申请的公开标识
        #     decision: 管理员对纠错申请作出的批准或拒绝决定
        #     actor_id: 本次变更操作者的标识
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        #     request_hash: 纠错决定请求内容摘要,用于识别幂等键冲突
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:联系方式纠错申请
        async with self._lock:
            idempotency_scope = (actor_id, idempotency_key)
            previous = self.decision_idempotency.get(idempotency_scope)
            if previous is not None:
                previous_hash, previous_correction_id = previous
                if previous_hash != request_hash or previous_correction_id != correction_id:
                    raise AppError("IDEMPOTENCY_KEY_REUSED", "幂等键已用于其他请求", 409)
                return self.corrections[previous_correction_id]
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
                if record.contact_status == "CONTACTED":
                    record.contact_status = "PENDING"
            self.decision_idempotency[idempotency_scope] = (request_hash, correction_id)
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
        # 功能:修改联系方式业务状态并记录管理员审计事件
        # 参数:
        #     self: 当前联系方式的InMemoryContactRepository实例
        #     user_id: 当前操作所属用户的公开标识
        #     status: 管理员指定的新联系方式业务状态
        #     actor_id: 本次变更操作者的标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:加密联系方式与变更状态记录
        async with self._lock:
            record = self._required(user_id)
            record.contact_status = status
            record.updated_at = now
            self._audit(user_id, "CONTACT_STATUS_CHANGED", "ADMIN", actor_id, now)
            return record

    def _admin_correction(self, correction: CorrectionRequest) -> AdminCorrectionRecord:
        # 功能:组装管理员查看的联系方式纠错记录与时间线
        # 参数:
        #     self: 当前联系方式的InMemoryContactRepository实例
        #     correction: 待转换或审核的联系方式纠错申请
        # 返回:管理端纠错申请与用户关联记录
        contact = self.records.get(correction.user_id)
        juya_number, nickname = self.admin_identities.get(
            correction.user_id, (correction.user_id, None)
        )
        timeline = tuple(
            ContactTimelineEvent(
                status=(
                    self.records[correction.user_id].contact_status
                    if correction.user_id in self.records
                    else "NOT_PROVIDED"
                ),
                actor_type=event.actor_type,
                actor_id=event.actor_id,
                event_type=event.event_type,
                occurred_at=event.occurred_at,
            )
            for event in self.audit_events
            if event.user_id == correction.user_id
        )
        return AdminCorrectionRecord(
            id=correction.public_id,
            user_id=correction.user_id,
            juya_number=juya_number,
            nickname=nickname,
            wechat_id_ciphertext=(None if contact is None else contact.wechat_id_ciphertext),
            reason=correction.reason,
            status=correction.status,
            created_at=correction.created_at,
            processed_at=correction.processed_at,
            timeline=timeline,
        )

    async def verify_change(self, user_id: str, actor_id: str, now: datetime) -> ContactRecord:
        # 功能:记录管理员对联系方式真实变更的核验
        # 参数:
        #     self: 当前联系方式的InMemoryContactRepository实例
        #     user_id: 当前操作所属用户的公开标识
        #     actor_id: 本次变更操作者的标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:加密联系方式与变更状态记录
        async with self._lock:
            record = self._required(user_id)
            record.change_pending = False
            record.verified_at = now
            record.verified_by = actor_id
            record.updated_at = now
            self._audit(user_id, "CONTACT_CHANGE_VERIFIED", "ADMIN", actor_id, now)
            return record

    def _required(self, user_id: str) -> ContactRecord:
        # 功能:读取用户联系方式记录并在不存在时抛出业务异常
        # 参数:
        #     self: 当前联系方式的InMemoryContactRepository实例
        #     user_id: 当前操作所属用户的公开标识
        # 返回:加密联系方式与变更状态记录
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
        # 功能:记录联系方式变更的操作者与审计事件
        # 参数:
        #     self: 当前联系方式的InMemoryContactRepository实例
        #     user_id: 当前操作所属用户的公开标识
        #     event_type: 统计或发件箱事件的业务类别
        #     actor_type: 本次变更操作者的角色类别
        #     actor_id: 本次变更操作者的标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:无返回值。
        self.audit_events.append(ContactAuditEvent(user_id, event_type, actor_type, actor_id, now))


def _database_datetime(value: datetime) -> datetime:
    # 功能:将时间转换为数据库保存的无时区UTC时间
    # 参数:
    #     value: 待转换时区的必填数据库或业务时间
    # 返回:转换后的无时区UTC时间
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _utc_datetime(value: datetime | None) -> datetime | None:
    # 功能:将数据库时间统一为带UTC时区的时间并保留空值
    # 参数:
    #     value: 待转换时区的数据库或业务时间;空值保留为空
    # 返回:带UTC时区的时间;原值为空时返回None
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class SQLAlchemyContactRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        # 功能:初始化联系方式的SQLAlchemyContactRepository对象并保存所需依赖与配置
        # 参数:
        #     self: 当前联系方式的SQLAlchemyContactRepository实例
        #     session_factory: 创建数据库事务会话的异步工厂
        # 返回:无返回值。
        self._session_factory = session_factory

    async def record_prompt_exposure(
        self,
        user_id: str,
        idempotency_key: str,
        now: datetime,
    ) -> bool:
        # 功能:幂等保存用户联系方式引导曝光来源与分组
        # 参数:
        #     self: 当前联系方式的SQLAlchemyContactRepository实例
        #     user_id: 当前操作所属用户的公开标识
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:是否为用户首次成功登记引导曝光
        async with self._session_factory() as session, session.begin():
            internal_id = await self._lock_user(session, user_id)
            if await self._has_prompt_exposure(session, user_id):
                return False
            return await append_contact_event(
                session,
                payload={"contact_cohort": uuid4().hex},
                event_key=f"contact-exposure:{internal_id}:{idempotency_key}",
                event_type="CONTACT_PROMPT_EXPOSED",
                user_id=internal_id,
                occurred_at=now,
            )

    async def has_prompt_exposure(self, user_id: str) -> bool:
        # 功能:查询用户是否已展示过联系方式引导
        # 参数:
        #     self: 当前联系方式的SQLAlchemyContactRepository实例
        #     user_id: 当前操作所属用户的公开标识
        # 返回:该用户是否已有联系方式引导曝光
        async with self._session_factory() as session:
            return await self._has_prompt_exposure(session, user_id)

    @staticmethod
    async def _has_prompt_exposure(session: AsyncSession, user_id: str) -> bool:
        # 功能:查询用户是否已产生联系方式引导曝光记录
        # 参数:
        #     session: 异步数据库会话
        #     user_id: 当前操作所属用户的公开标识
        # 返回:该用户是否已有联系方式引导曝光
        exposed = await session.scalar(
            text(
                "SELECT 1 FROM analytics_event e JOIN user_account u ON u.id=e.user_id "
                "WHERE u.public_id=:user AND e.event_type='CONTACT_PROMPT_EXPOSED' LIMIT 1"
            ),
            {"user": user_id},
        )
        return exposed is not None

    async def get_contact(self, user_id: str) -> ContactRecord | None:
        # 功能:读取当前用户联系方式状态
        # 参数:
        #     self: 当前联系方式的SQLAlchemyContactRepository实例
        #     user_id: 当前操作所属用户的公开标识
        # 返回:加密联系方式与变更状态记录;不存在或无候选时返回None
        async with self._session_factory() as session:
            return await self._load_contact(session, user_id)

    async def get_contacts(self, user_ids: tuple[str, ...]) -> tuple[ContactRecord, ...]:
        # 功能:批量读取用户联系方式记录并保持输入顺序
        # 参数:
        #     self: 当前联系方式的SQLAlchemyContactRepository实例
        #     user_ids: 需要批量查询联系方式的用户公开标识序列
        # 返回:加密联系方式与变更状态记录集合,保持输入的用户顺序
        if not user_ids:
            return ()
        placeholders: list[str] = []
        parameters: dict[str, object] = {}
        for index, user_id in enumerate(user_ids):
            name = f"user_id_{index}"
            placeholders.append(f":{name}")
            parameters[name] = user_id
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT u.public_id, c.wechat_id_ciphertext, c.wechat_id_hmac, "
                            "c.consent_version, c.consented_at, c.source, c.self_edit_count, "
                            "c.withdrawn_at, c.change_pending, c.contact_status, c.verified_at, "
                            "c.verified_by, c.updated_at FROM user_contact c "
                            "JOIN user_account u ON u.id = c.user_id WHERE u.public_id IN ("
                            + ",".join(placeholders)
                            + ")"
                        ),
                        parameters,
                    )
                )
                .mappings()
                .all()
            )
        by_user_id = {str(row["public_id"]): self._contact_from_row(row) for row in rows}
        return tuple(by_user_id[user_id] for user_id in user_ids if user_id in by_user_id)

    async def find_user_by_hmac(self, lookup_hmac: bytes) -> str | None:
        # 功能:按微信号检索摘要查找所属用户
        # 参数:
        #     self: 当前联系方式的SQLAlchemyContactRepository实例
        #     lookup_hmac: 标准化微信号的不可逆检索摘要
        # 返回:微信联系方式所属用户公开标识;未找到时为None
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
        # 功能:保存联系方式并限制真实变更次数与纠错状态
        # 参数:
        #     self: 当前联系方式的SQLAlchemyContactRepository实例
        #     user_id: 当前操作所属用户的公开标识
        #     ciphertext: 待解密的敏感字段密文字节
        #     lookup_hmac: 标准化微信号的不可逆检索摘要
        #     consent_version: 用户保存联系方式时同意的隐私条款版本
        #     source: 用户保存微信联系方式的来源页面或入口
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:加密联系方式与变更状态记录
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
                    if record.contact_status == "CONTACTED" or record.verified_at:
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
        # 功能:撤回联系方式并删除加密内容与检索摘要
        # 参数:
        #     self: 当前联系方式的SQLAlchemyContactRepository实例
        #     user_id: 当前操作所属用户的公开标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:加密联系方式与变更状态记录
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
                emit_analytics=record is not None and record.wechat_id_hmac is not None,
                analytics_payload={
                    "cohort_day": record.consented_at.astimezone(UTC)
                    .astimezone(ZoneInfo("Asia/Shanghai"))
                    .date()
                    .isoformat()
                }
                if record is not None and record.consented_at
                else None,
            )
            withdrawn = await self._load_contact(session, user_id)
            if withdrawn is None:
                raise RuntimeError("Contact withdrawal was not visible")
            return withdrawn

    async def create_correction(
        self, user_id: str, reason: str, now: datetime
    ) -> CorrectionRequest:
        # 功能:创建联系方式纠错申请并限制重复有效申请
        # 参数:
        #     self: 当前联系方式的SQLAlchemyContactRepository实例
        #     user_id: 当前操作所属用户的公开标识
        #     reason: 撤销、纠错或反馈异议的业务原因说明
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:联系方式纠错申请
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

    async def list_corrections(
        self, status: str | None, page: int, page_size: int
    ) -> tuple[tuple[AdminCorrectionRecord, ...], int]:
        # 功能:分页筛选管理员可见的联系方式纠错申请
        # 参数:
        #     self: 当前联系方式的SQLAlchemyContactRepository实例
        #     status: 纠错申请状态筛选条件; None表示不按状态筛选
        #     page: 管理端纠错申请列表的页码
        #     page_size: 管理端纠错申请每页最多返回的记录数
        # 返回:本页纠错申请记录集合及符合条件的总记录数量
        where = " WHERE r.status = :status" if status is not None else ""
        parameters: dict[str, object] = {
            "status": status,
            "limit": page_size,
            "offset": (page - 1) * page_size,
        }
        async with self._session_factory() as session:
            total = int(
                await session.scalar(
                    text("SELECT COUNT(*) FROM contact_correction_request r" + where),
                    parameters,
                )
                or 0
            )
            rows = (
                (
                    await session.execute(
                        text(self._admin_correction_select() + where + self._admin_order_limit()),
                        parameters,
                    )
                )
                .mappings()
                .all()
            )
        return tuple(self._admin_correction_from_row(row, ()) for row in rows), total

    async def get_correction(self, correction_id: str) -> AdminCorrectionRecord | None:
        # 功能:读取联系方式纠错申请的管理端详情
        # 参数:
        #     self: 当前联系方式的SQLAlchemyContactRepository实例
        #     correction_id: 联系方式纠错申请的公开标识
        # 返回:管理端纠错申请与用户关联记录;不存在或无候选时返回None
        async with self._session_factory() as session:
            row = (
                (
                    await session.execute(
                        text(self._admin_correction_select() + " WHERE r.public_id = :public_id"),
                        {"public_id": correction_id},
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            timeline = await self._load_timeline(session, int(row["internal_user_id"]))
        return self._admin_correction_from_row(row, timeline)

    async def decide_correction(
        self,
        correction_id: str,
        decision: str,
        actor_id: str,
        idempotency_key: str,
        request_hash: str,
        now: datetime,
    ) -> CorrectionRequest:
        # 功能:幂等处理管理员的联系方式纠错批准或拒绝决定
        # 参数:
        #     self: 当前联系方式的SQLAlchemyContactRepository实例
        #     correction_id: 联系方式纠错申请的公开标识
        #     decision: 管理员对纠错申请作出的批准或拒绝决定
        #     actor_id: 本次变更操作者的标识
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        #     request_hash: 纠错决定请求内容摘要,用于识别幂等键冲突
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:联系方式纠错申请
        try:
            async with self._session_factory() as session, session.begin():
                replay = await self._load_decision_by_key(
                    session, actor_id, idempotency_key, for_update=True
                )
                if replay is not None:
                    return self._validate_replay(
                        replay, correction_id=correction_id, request_hash=request_hash
                    )
                row = (
                    (
                        await session.execute(
                            text(
                                "SELECT r.user_id AS internal_user_id, r.reason, r.status, "
                                "r.created_at, u.public_id AS user_public_id "
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
                        "processed_at = :now, processed_by = :actor_id, "
                        "decision_idempotency_key = :idempotency_key, "
                        "decision_request_hash = :request_hash WHERE public_id = :public_id"
                    ),
                    {
                        "decision": decision,
                        "now": _database_datetime(now),
                        "actor_id": actor_id,
                        "idempotency_key": idempotency_key,
                        "request_hash": request_hash,
                        "public_id": correction_id,
                    },
                )
                if decision == "APPROVED":
                    await session.execute(
                        text(
                            "UPDATE user_contact SET self_edit_count = 0, verified_at = NULL, "
                            "verified_by = NULL, contact_status = CASE "
                            "WHEN contact_status = 'CONTACTED' THEN 'PENDING' "
                            "ELSE contact_status END WHERE user_id = :user_id"
                        ),
                        {"user_id": row["internal_user_id"]},
                    )
                await self._history(
                    session,
                    row["internal_user_id"],
                    "PENDING",
                    "ADMIN",
                    actor_id,
                    now,
                    f"CONTACT_CORRECTION_{decision}",
                )
                return CorrectionRequest(
                    correction_id,
                    row["user_public_id"],
                    row["reason"],
                    decision,
                    _utc_datetime(row["created_at"]),
                    now,
                )
        except IntegrityError as error:
            async with self._session_factory() as session:
                replay = await self._load_decision_by_key(session, actor_id, idempotency_key)
            if replay is None:
                raise
            try:
                return self._validate_replay(
                    replay, correction_id=correction_id, request_hash=request_hash
                )
            except AppError as replay_error:
                raise replay_error from error

    async def update_status(
        self, user_id: str, status: str, actor_id: str, now: datetime
    ) -> ContactRecord:
        # 功能:修改联系方式业务状态并记录管理员审计事件
        # 参数:
        #     self: 当前联系方式的SQLAlchemyContactRepository实例
        #     user_id: 当前操作所属用户的公开标识
        #     status: 管理员指定的新联系方式业务状态
        #     actor_id: 本次变更操作者的标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:加密联系方式与变更状态记录
        async with self._session_factory() as session, session.begin():
            internal_id = await self._lock_user(session, user_id)
            if await self._load_contact(session, user_id, for_update=True) is None:
                raise AppError("CONTACT_NOT_FOUND", "联系方式不存在", 404)
            await session.execute(
                text(
                    "UPDATE user_contact SET contact_status = :status, updated_at = :now "
                    "WHERE user_id = :user_id"
                ),
                {
                    "status": status,
                    "now": _database_datetime(now),
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
        # 功能:记录管理员对联系方式真实变更的核验
        # 参数:
        #     self: 当前联系方式的SQLAlchemyContactRepository实例
        #     user_id: 当前操作所属用户的公开标识
        #     actor_id: 本次变更操作者的标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:加密联系方式与变更状态记录
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
        # 功能:锁定用户账号行并取得数据库内部主键
        # 参数:
        #     session: 异步数据库会话
        #     public_id: 当前操作所属用户账号的公开标识
        # 返回:已锁定用户账号的数据库内部主键
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
        # 功能:读取指定用户的联系方式记录并按需加锁
        # 参数:
        #     session: 异步数据库会话
        #     public_id: 当前操作所属用户账号的公开标识
        #     for_update: 是否锁定查询记录以防止并发状态变更
        # 返回:加密联系方式与变更状态记录;不存在或无候选时返回None
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
        return SQLAlchemyContactRepository._contact_from_row(row)

    @staticmethod
    def _contact_from_row(row: RowMapping) -> ContactRecord:
        # 功能:将联系方式数据库行转换为领域记录
        # 参数:
        #     row: 查询返回的联系方式数据库字段映射
        # 返回:加密联系方式与变更状态记录
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
    def _admin_correction_select() -> str:
        # 功能:生成纠错申请及用户关联信息的查询语句
        # 参数:
        #     无形参。
        # 返回:纠错详情数据库查询SQL文本
        return (
            "SELECT r.id AS correction_internal_id, r.public_id AS correction_public_id, "
            "r.user_id AS internal_user_id, r.reason, r.status, r.created_at, r.processed_at, "
            "u.public_id AS user_public_id, u.juya_number, p.nickname, "
            "c.wechat_id_ciphertext FROM contact_correction_request r "
            "JOIN user_account u ON u.id = r.user_id "
            "LEFT JOIN user_profile p ON p.user_id = u.id "
            "LEFT JOIN user_contact c ON c.user_id = u.id"
        )

    @staticmethod
    def _admin_order_limit() -> str:
        # 功能:生成纠错申请列表的排序和分页限制语句
        # 参数:
        #     无形参。
        # 返回:纠错列表排序和分页SQL片段
        return " ORDER BY r.created_at DESC, r.id DESC LIMIT :limit OFFSET :offset"

    @staticmethod
    def _admin_correction_from_row(
        row: RowMapping, timeline: tuple[ContactTimelineEvent, ...]
    ) -> AdminCorrectionRecord:
        # 功能:将纠错申请数据库行与时间线转换为管理端投影
        # 参数:
        #     row: 查询返回的联系方式数据库字段映射
        #     timeline: 联系方式纠错详情包含的审计时间线
        # 返回:管理端纠错申请与用户关联记录
        return AdminCorrectionRecord(
            id=row["correction_public_id"],
            user_id=row["user_public_id"],
            juya_number=row["juya_number"],
            nickname=row["nickname"],
            wechat_id_ciphertext=row["wechat_id_ciphertext"],
            reason=row["reason"],
            status=row["status"],
            created_at=_utc_datetime(row["created_at"]),
            processed_at=_utc_datetime(row["processed_at"]),
            timeline=timeline,
        )

    @staticmethod
    async def _load_timeline(
        session: AsyncSession, user_id: int
    ) -> tuple[ContactTimelineEvent, ...]:
        # 功能:读取用户联系方式的状态变更时间线
        # 参数:
        #     session: 异步数据库会话
        #     user_id: 业务数据库中的用户内部数值主键
        # 返回:按时间排列的联系方式状态变更事件集合
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT status, actor_type, actor_id, occurred_at, note "
                        "FROM contact_status_history WHERE user_id = :user_id "
                        "ORDER BY occurred_at ASC, id ASC"
                    ),
                    {"user_id": user_id},
                )
            )
            .mappings()
            .all()
        )
        return tuple(
            ContactTimelineEvent(
                status=row["status"],
                actor_type=row["actor_type"],
                actor_id=row["actor_id"],
                event_type=(
                    row["note"] if row["note"] in _SAFE_CONTACT_EVENT_TYPES else "CONTACT_EVENT"
                ),
                occurred_at=_utc_datetime(row["occurred_at"]) or datetime.now(UTC),
            )
            for row in rows
        )

    @staticmethod
    async def _load_decision_by_key(
        session: AsyncSession,
        actor_id: str,
        idempotency_key: str,
        *,
        for_update: bool = False,
    ) -> RowMapping | None:
        # 功能:按管理员和幂等键查找已处理的纠错决定
        # 参数:
        #     session: 异步数据库会话
        #     actor_id: 本次变更操作者的标识
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        #     for_update: 是否锁定查询记录以防止并发状态变更
        # 返回:数据库查询行字段映射;不存在或无候选时返回None
        suffix = " FOR UPDATE" if for_update else ""
        return (
            (
                await session.execute(
                    text(
                        "SELECT r.public_id AS correction_public_id, r.reason, r.status, "
                        "r.created_at, r.processed_at, r.decision_request_hash, "
                        "u.public_id AS user_public_id FROM contact_correction_request r "
                        "JOIN user_account u ON u.id = r.user_id "
                        "WHERE r.processed_by = :actor_id "
                        "AND r.decision_idempotency_key = :idempotency_key" + suffix
                    ),
                    {"actor_id": actor_id, "idempotency_key": idempotency_key},
                )
            )
            .mappings()
            .first()
        )

    @staticmethod
    def _validate_replay(
        row: RowMapping, *, correction_id: str, request_hash: str
    ) -> CorrectionRequest:
        # 功能:校验重复纠错决定的请求内容与原幂等记录一致
        # 参数:
        #     row: 查询返回的联系方式数据库字段映射
        #     correction_id: 联系方式纠错申请的公开标识
        #     request_hash: 纠错决定请求内容摘要,用于识别幂等键冲突
        # 返回:联系方式纠错申请
        if (
            row["correction_public_id"] != correction_id
            or row["decision_request_hash"] != request_hash
        ):
            raise AppError("IDEMPOTENCY_KEY_REUSED", "幂等键已用于其他请求", 409)
        return CorrectionRequest(
            public_id=row["correction_public_id"],
            user_id=row["user_public_id"],
            reason=row["reason"],
            status=row["status"],
            created_at=_utc_datetime(row["created_at"]),
            processed_at=_utc_datetime(row["processed_at"]),
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
        *,
        emit_analytics: bool = True,
        analytics_payload: dict[str, object] | None = None,
    ) -> None:
        # 功能:写入联系方式状态历史并按需同步匿名统计事件
        # 参数:
        #     session: 异步数据库会话
        #     user_id: 业务数据库中的用户内部数值主键
        #     status: 本次联系方式变更完成后的业务状态,写入状态历史
        #     actor_type: 本次变更操作者的角色类别
        #     actor_id: 本次变更操作者的标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        #     note: 联系方式审计事件的说明或业务动作代码
        #     emit_analytics: 是否同时写入联系方式匿名统计事件
        #     analytics_payload: 随联系方式状态变更写入的匿名统计字段
        # 返回:无返回值。
        event_type = {
            "CONTACT_CREATED": "CONTACT_SUBMITTED",
            "CONTACT_CHANGED": "CONTACT_CHANGED",
            "CONTACT_WITHDRAWN": "CONTACT_WITHDRAWN",
            "CONTACT_STATUS_CHANGED": "CONTACT_STATUS_CHANGED",
        }.get(note)
        extra = dict(analytics_payload or {})
        if note == "CONTACT_CHANGED":
            previous = await session.scalar(
                text(
                    "SELECT 1 FROM contact_status_history WHERE user_id=:user AND note IN "
                    "('CONTACT_CREATED','CONTACT_CHANGED') LIMIT 1"
                ),
                {"user": user_id},
            )
            if previous is None:
                event_type = "CONTACT_SUBMITTED"
                note = "CONTACT_CREATED"
        if event_type in {"CONTACT_SUBMITTED", "CONTACT_WITHDRAWN"}:
            reference_type = (
                "CONTACT_PROMPT_EXPOSED"
                if event_type == "CONTACT_SUBMITTED"
                else "CONTACT_SUBMITTED"
            )
            reference_row = (
                await session.execute(
                    text(
                        "SELECT occurred_at,payload FROM analytics_event WHERE user_id=:user "
                        "AND event_type=:type ORDER BY occurred_at DESC,id DESC LIMIT 1"
                    ),
                    {"user": user_id, "type": reference_type},
                )
            ).first()
            reference = reference_row.occurred_at if reference_row is not None else None
            if reference_row is not None:
                reference_payload = (
                    json.loads(reference_row.payload)
                    if isinstance(reference_row.payload, str)
                    else reference_row.payload or {}
                )
                cohort = reference_payload.get("contact_cohort")
                if cohort:
                    extra["contact_cohort"] = cohort
            if event_type == "CONTACT_SUBMITTED":
                extra["prompted"] = reference is not None
            if reference is not None:
                extra["cohort_day"] = (
                    reference.replace(tzinfo=UTC)
                    .astimezone(ZoneInfo("Asia/Shanghai"))
                    .date()
                    .isoformat()
                )
        if event_type is not None and emit_analytics:
            await append_contact_event(
                session,
                event_key=f"contact-first:{user_id}"
                if event_type == "CONTACT_SUBMITTED"
                else f"contact:{new_ulid(now)}",
                event_type=event_type,
                user_id=user_id,
                occurred_at=now,
                payload={"status": status, **extra},
            )
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
