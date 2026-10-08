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
    # 功能:去除微信号空白并统一大小写以比较真实变更
    # 参数:
    #     value: 待标准化以比较真实变更的用户微信号
    # 返回:去除空白并统一大小写后的微信号
    return unicodedata.normalize("NFKC", value).strip().casefold()


class ContactService:
    def __init__(self, repository: ContactRepository, field_cipher: FieldCipher) -> None:
        # 功能:初始化联系方式保存、撤回和纠错服务并保存所需依赖与配置
        # 参数:
        #     self: 当前联系方式保存、撤回和纠错服务实例
        #     repository: 联系方式与纠错申请仓库,承载联系方式业务操作
        #     field_cipher: 加解密微信身份并生成检索摘要的字段保护服务
        # 返回:无返回值。
        self._repository = repository
        self._field_cipher = field_cipher

    async def record_prompt_exposure(
        self,
        user_id: str,
        idempotency_key: str,
        now: datetime,
    ) -> bool:
        # 功能:幂等保存用户联系方式引导曝光来源与分组
        # 参数:
        #     self: 当前联系方式保存、撤回和纠错服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:是否为用户首次成功登记引导曝光
        if not idempotency_key or len(idempotency_key) > 128:
            raise AppError("IDEMPOTENCY_KEY_INVALID", "幂等键无效", 422)
        return await self._repository.record_prompt_exposure(user_id, idempotency_key, now)

    async def has_prompt_exposure(self, user_id: str) -> bool:
        # 功能:查询用户是否已展示过联系方式引导
        # 参数:
        #     self: 当前联系方式保存、撤回和纠错服务实例
        #     user_id: 当前操作所属用户的公开标识
        # 返回:该用户是否已有联系方式引导曝光
        return await self._repository.has_prompt_exposure(user_id)

    async def get(self, user_id: str) -> ContactView | None:
        # 功能:读取并解密当前用户联系方式状态
        # 参数:
        #     self: 当前联系方式保存、撤回和纠错服务实例
        #     user_id: 当前操作所属用户的公开标识
        # 返回:用户可见联系方式与修改资格;不存在或无候选时返回None
        record = await self._repository.get_contact(user_id)
        return self._view(record) if record is not None else None

    async def get_many(self, user_ids: tuple[str, ...]) -> tuple[ContactView, ...]:
        # 功能:批量获取用户联系方式可见投影
        # 参数:
        #     self: 当前联系方式保存、撤回和纠错服务实例
        #     user_ids: 需要批量查询联系方式的用户公开标识序列
        # 返回:用户可见联系方式与修改资格集合,保持输入的用户顺序
        if len(user_ids) > 100:
            raise AppError("CONTACT_PROJECTION_LIMIT", "批量查询最多支持100个用户", 422)
        return tuple(self._view(record) for record in await self._repository.get_contacts(user_ids))

    async def find_user_by_wechat_id(self, wechat_id: str) -> str | None:
        # 功能:标准化微信号并通过摘要查找所属用户
        # 参数:
        #     self: 当前联系方式保存、撤回和纠错服务实例
        #     wechat_id: 用户主动提交的微信联系方式
        # 返回:微信联系方式所属用户公开标识;未找到时为None
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
        # 功能:校验微信号真实变更并保存联系方式与用户同意版本
        # 参数:
        #     self: 当前联系方式保存、撤回和纠错服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     wechat_id: 用户主动提交的微信联系方式
        #     consent_version: 用户保存联系方式时同意的隐私条款版本
        #     source: 用户保存微信联系方式的来源页面或入口
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:用户可见联系方式与修改资格
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
        # 功能:撤回联系方式并删除加密内容与检索摘要
        # 参数:
        #     self: 当前联系方式保存、撤回和纠错服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:用户可见联系方式与修改资格
        return self._view(await self._repository.withdraw(user_id, now))

    async def request_correction(
        self, user_id: str, reason: str, now: datetime
    ) -> CorrectionRequest:
        # 功能:为已联系的用户创建唯一有效的联系方式纠错申请
        # 参数:
        #     self: 当前联系方式保存、撤回和纠错服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     reason: 撤销、纠错或反馈异议的业务原因说明
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:联系方式纠错申请
        normalized_reason = reason.strip()
        if not 2 <= len(normalized_reason) <= 500:
            raise AppError("CORRECTION_REASON_INVALID", "更正原因长度不正确", 422)
        return await self._repository.create_correction(user_id, normalized_reason, now)

    async def list_corrections(
        self, status: str | None, page: int, page_size: int
    ) -> tuple[tuple[AdminCorrectionView, ...], int]:
        # 功能:分页筛选管理员可见的联系方式纠错申请
        # 参数:
        #     self: 当前联系方式保存、撤回和纠错服务实例
        #     status: 纠错申请状态筛选条件; None表示不按状态筛选
        #     page: 管理端纠错申请列表的页码
        #     page_size: 管理端纠错申请每页最多返回的记录数
        # 返回:本页纠错申请记录集合及符合条件的总记录数量
        if status is not None and status not in _CORRECTION_STATUSES:
            raise AppError("CORRECTION_STATUS_INVALID", "更正申请状态无效", 422)
        if page < 1 or not 1 <= page_size <= 100:
            raise AppError("CORRECTION_PAGE_INVALID", "分页参数无效", 422)
        records, total = await self._repository.list_corrections(status, page, page_size)
        return tuple(self._admin_view(item) for item in records), total

    async def get_correction(self, correction_id: str) -> AdminCorrectionView:
        # 功能:读取联系方式纠错申请的管理端详情
        # 参数:
        #     self: 当前联系方式保存、撤回和纠错服务实例
        #     correction_id: 联系方式纠错申请的公开标识
        # 返回:管理员可见纠错详情与时间线
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
        # 功能:幂等处理管理员的联系方式纠错批准或拒绝决定
        # 参数:
        #     self: 当前联系方式保存、撤回和纠错服务实例
        #     correction_id: 联系方式纠错申请的公开标识
        #     decision: 管理员对纠错申请作出的批准或拒绝决定
        #     actor_id: 本次变更操作者的标识
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:联系方式纠错申请
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
        # 功能:修改联系方式业务状态并记录管理员审计事件
        # 参数:
        #     self: 当前联系方式保存、撤回和纠错服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     status: 管理员指定的新联系方式业务状态
        #     actor_id: 本次变更操作者的标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:用户可见联系方式与修改资格
        if status not in _CONTACT_STATUSES:
            raise AppError("CONTACT_STATUS_INVALID", "联系状态无效", 422)
        return self._view(await self._repository.update_status(user_id, status, actor_id, now))

    async def verify_change(self, user_id: str, actor_id: str, now: datetime) -> ContactView:
        # 功能:记录管理员对联系方式真实变更的核验
        # 参数:
        #     self: 当前联系方式保存、撤回和纠错服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     actor_id: 本次变更操作者的标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:用户可见联系方式与修改资格
        return self._view(await self._repository.verify_change(user_id, actor_id, now))

    def _view(self, record: ContactRecord) -> ContactView:
        # 功能:解密联系方式并组装用户可见的联系状态
        # 参数:
        #     self: 当前联系方式保存、撤回和纠错服务实例
        #     record: 加密联系方式与变更状态记录
        # 返回:用户可见联系方式与修改资格
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
        # 功能:将联系方式纠错记录转换为管理端可见详情
        # 参数:
        #     self: 当前联系方式保存、撤回和纠错服务实例
        #     record: 管理端纠错申请与用户关联记录
        # 返回:管理员可见纠错详情与时间线
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
