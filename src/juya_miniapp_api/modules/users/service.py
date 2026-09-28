from dataclasses import dataclass
from datetime import datetime

from juya_miniapp_api.modules.contacts.domain import ContactView
from juya_miniapp_api.modules.contacts.service import ContactService
from juya_miniapp_api.modules.users.models import UserProfile
from juya_miniapp_api.modules.users.repository import UserRepository
from juya_miniapp_api.shared.errors import AppError


@dataclass(frozen=True, slots=True)
class MeView:
    profile: UserProfile
    contact: ContactView | None


class UserService:
    def __init__(self, repository: UserRepository, contacts: ContactService) -> None:
        self._repository = repository
        self._contacts = contacts

    async def get_me(self, public_id: str) -> MeView:
        profile = await self._repository.get_profile(public_id)
        if profile is None:
            raise AppError("USER_NOT_FOUND", "用户不存在", 404)
        return MeView(profile, await self._contacts.get(public_id))

    async def update_profile(
        self,
        public_id: str,
        nickname: str | None,
        avatar_object_key: str | None,
        now: datetime,
    ) -> MeView:
        cleaned_nickname = nickname.strip() if nickname else None
        if cleaned_nickname == "":
            cleaned_nickname = None
        if avatar_object_key and "://" in avatar_object_key:
            raise AppError("AVATAR_OBJECT_KEY_INVALID", "头像对象键无效", 422)
        profile = await self._repository.update_profile(
            public_id, cleaned_nickname, avatar_object_key, now
        )
        return MeView(profile, await self._contacts.get(public_id))

    async def search(
        self, *, juya_number: str | None, nickname: str | None, limit: int = 50
    ) -> list[MeView]:
        profiles = await self._repository.search_profiles(
            juya_number=juya_number, nickname=nickname, limit=min(limit, 100)
        )
        return [MeView(item, await self._contacts.get(item.public_id)) for item in profiles]
