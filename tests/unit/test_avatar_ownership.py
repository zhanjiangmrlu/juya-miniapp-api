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
    # 功能:验证更新资料前拒绝不属于用户的头像对象
    # 参数:
    #     key: 待校验归属并固定的头像OSS对象键
    # 返回:无返回值;断言失败时由pytest报告测试失败
    service = UserService(None, None)
    with pytest.raises(AppError) as error:
        await service.update_profile("user", None, key, None)
    assert error.value.code == "AVATAR_OBJECT_KEY_INVALID"
