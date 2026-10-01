import pytest

from juya_miniapp_api.modules.users.service import UserService
from juya_miniapp_api.shared.errors import AppError


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key",
    [
        "avatars/other/photo.png",
        "uploads/images/admin/a.png",
        "avatars/user/../other.png",
        "https://example.com/a.png",
    ],
)
async def test_profile_rejects_unowned_avatar_before_repository_write(key: str) -> None:
    service = UserService(None, None)
    with pytest.raises(AppError) as error:
        await service.update_profile("user", None, key, None)
    assert error.value.code == "AVATAR_OBJECT_KEY_INVALID"
