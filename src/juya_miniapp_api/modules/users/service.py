from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from juya_miniapp_api.integrations.oss.avatar import owned_avatar_key
from juya_miniapp_api.modules.contacts.domain import ContactView
from juya_miniapp_api.modules.contacts.service import ContactService
from juya_miniapp_api.modules.users.models import UserProfile
from juya_miniapp_api.modules.users.repository import UserRepository
from juya_miniapp_api.shared.errors import AppError


@dataclass(frozen=True, slots=True)
class MeView:
    profile: UserProfile
    contact: ContactView | None
    contact_prompt_eligible: bool = False
    deletion: dict[str, object] | None = None


class UserService:
    def __init__(
        self,
        repository: UserRepository,
        contacts: ContactService,
        *,
        avatar_verifier: Callable[[str, str], Awaitable[str]] | None = None,
        open_completion_count: Callable[[str], Awaitable[int]] | None = None,
    ) -> None:
        # 功能:初始化用户资料查询更新服务并保存所需依赖与配置
        # 参数:
        #     self: 当前用户资料查询更新服务实例
        #     repository: 用户资料存储仓库,承载用户资料业务操作
        #     contacts: 查询和更新用户联系方式的业务服务
        #     avatar_verifier: 核验头像归属和图片内容并返回固定对象键的异步回调
        #     open_completion_count: 查询用户开放场景完成数量的异步回调
        # 返回:无返回值。
        self._repository = repository
        self._contacts = contacts
        self._avatar_verifier = avatar_verifier
        self._open_completion_count = open_completion_count

    async def get_me(self, public_id: str) -> MeView:
        # 功能:读取账号资料、联系方式与引导资格
        # 参数:
        #     self: 当前用户资料查询更新服务实例
        #     public_id: 当前操作所属用户账号的公开标识
        # 返回:用户资料、联系方式和引导资格投影
        profile = await self._repository.get_profile(public_id)
        if profile is None:
            raise AppError("USER_NOT_FOUND", "用户不存在", 404)
        contact = await self._contacts.get(public_id)
        eligible = (
            profile.status == "ACTIVE"
            and contact is None
            and self._open_completion_count is not None
            and await self._open_completion_count(public_id) >= 3
            and not await self._contacts.has_prompt_exposure(public_id)
        )
        deletion = (
            await self._repository.pending_deletion(public_id)
            if profile.status == "DELETION_PENDING"
            else None
        )
        return MeView(profile, contact, eligible, deletion)

    async def update_profile(
        self,
        public_id: str,
        nickname: str | None,
        avatar_object_key: str | None,
        now: datetime,
    ) -> MeView:
        # 功能:校验头像对象归属并更新用户昵称与头像资料
        # 参数:
        #     self: 当前用户资料查询更新服务实例
        #     public_id: 当前操作所属用户账号的公开标识
        #     nickname: 待保存的用户昵称; None表示未提供或清空昵称
        #     avatar_object_key: 用户头像在OSS中的对象键
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:用户资料、联系方式和引导资格投影
        cleaned_nickname = nickname.strip() if nickname else None
        if cleaned_nickname == "":
            cleaned_nickname = None
        if avatar_object_key:
            if not owned_avatar_key(public_id, avatar_object_key):
                raise AppError("AVATAR_OBJECT_KEY_INVALID", "头像对象键无效", 422)
            if self._avatar_verifier is None:
                raise AppError("AVATAR_STORAGE_UNAVAILABLE", "头像校验暂不可用", 503)
            avatar_object_key = await self._avatar_verifier(public_id, avatar_object_key)
        profile = await self._repository.update_profile(
            public_id, cleaned_nickname, avatar_object_key, now
        )
        return MeView(profile, await self._contacts.get(public_id))

    async def search(
        self, *, juya_number: str | None, nickname: str | None, limit: int = 50
    ) -> list[MeView]:
        # 功能:按句芽编号或昵称搜索用户公开资料
        # 参数:
        #     self: 当前用户资料查询更新服务实例
        #     juya_number: 用户对外展示的句芽编号,亦可作为搜索条件
        #     nickname: 需要匹配的昵称搜索文本; None表示不按昵称筛选
        #     limit: 本次查询或任务领取允许的最大记录数量
        # 返回:用户资料、联系方式和引导资格投影集合
        profiles = await self._repository.search_profiles(
            juya_number=juya_number, nickname=nickname, limit=min(limit, 100)
        )
        return [MeView(item, await self._contacts.get(item.public_id)) for item in profiles]
