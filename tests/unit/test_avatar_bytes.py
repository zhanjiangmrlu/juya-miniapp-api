from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from juya_miniapp_api.integrations.oss.avatar import AvatarStore
from juya_miniapp_api.shared.errors import AppError


class AlreadyExists(Exception):
    code = "FileAlreadyExists"


class WrappedFailure(Exception):
    def unwrap(self) -> Exception:
        return AlreadyExists()


class Client:
    def __init__(self, data: bytes, *, collision: bool = False) -> None:
        self.data = data
        self.closed = False
        self.writes: list[object] = []
        self.collision = collision

    def get_object(self, request: object) -> object:
        def close() -> None:
            self.closed = True

        return SimpleNamespace(
            content_length=len(self.data),
            body=SimpleNamespace(iter_bytes=lambda **kwargs: iter([self.data]), close=close),
        )

    def put_object(self, request: object) -> None:
        self.writes.append(request)
        if self.collision:
            raise WrappedFailure()


def store(client: Client) -> AvatarStore:
    value = object.__new__(AvatarStore)
    value.client = client
    value.bucket = "test-bucket"
    return value


@pytest.mark.asyncio
async def test_invalid_image_bytes_are_rejected_before_fixed_write() -> None:
    client = Client(b"forged png bytes")
    with pytest.raises(AppError) as failure:
        await store(client).confirm("user", "uploads/avatars/user/photo.png")
    assert failure.value.code == "AVATAR_IMAGE_INVALID"
    assert client.closed and not client.writes


@pytest.mark.asyncio
@pytest.mark.parametrize("collision", [False, True])
async def test_verified_avatar_is_normalized_and_fixed_with_safe_retries(collision: bool) -> None:
    stream = BytesIO()
    Image.new("RGB", (10, 12), "blue").save(stream, "JPEG")
    client = Client(stream.getvalue(), collision=collision)
    key = await store(client).confirm("user", "uploads/avatars/user/photo.jpg")
    assert key.startswith("avatars/user/") and key.endswith(".png")
    assert client.closed and len(client.writes) == 1
    request = client.writes[0]
    assert request.key == key and request.acl == "private" and request.forbid_overwrite == "true"
    with Image.open(BytesIO(request.body)) as image:
        assert image.format == "PNG" and image.size == (10, 12)
