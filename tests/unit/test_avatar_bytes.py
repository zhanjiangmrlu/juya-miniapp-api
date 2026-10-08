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
        # 功能:解包模拟OSS对象冲突异常供固定头像重试判断
        # 参数:
        #     self: 当前小程序的WrappedFailure实例
        # 返回:模拟OSS对象已存在错误的内部异常
        return AlreadyExists()


class Client:
    def __init__(self, data: bytes, *, collision: bool = False) -> None:
        # 功能:初始化同步HTTP或OSS客户端并保存所需依赖与配置
        # 参数:
        #     self: 当前同步HTTP或OSS客户端实例
        #     data: 测试或头像验证读取的原始图片字节
        #     collision: 是否在头像固定写入测试中模拟对象键冲突
        # 返回:无返回值。
        self.data = data
        self.closed = False
        self.writes: list[object] = []
        self.collision = collision

    def get_object(self, request: object) -> object:
        # 功能:在测试中模拟OSS读取对象并返回测试媒体内容
        # 参数:
        #     self: 当前同步HTTP或OSS客户端实例
        #     request: OSS SDK读取或写入测试对象的请求模型
        # 返回:带内容长度、图片字节迭代器和关闭回调的OSS读取响应桩
        def close() -> None:
            # 功能:标记测试OSS响应体已关闭以验证资源释放
            # 参数:
            #     无形参。
            # 返回:无返回值。
            self.closed = True

        # 匿名函数: iter_bytes模拟OSS响应体按块迭代原始测试图片
        # 参数:
        #     kwargs: OSS字节迭代接口传入的可选参数; 当前测试桩不依赖这些选项
        # 返回: 产出测试图片字节的一次性迭代器
        return SimpleNamespace(
            content_length=len(self.data),
            body=SimpleNamespace(iter_bytes=lambda **kwargs: iter([self.data]), close=close),
        )

    def put_object(self, request: object) -> None:
        # 功能:在测试中模拟OSS写入并收集标准化头像对象供断言
        # 参数:
        #     self: 当前同步HTTP或OSS客户端实例
        #     request: OSS SDK读取或写入测试对象的请求模型
        # 返回:无返回值。
        self.writes.append(request)
        if self.collision:
            raise WrappedFailure()


def store(client: Client) -> AvatarStore:
    # 功能:构造注入测试OSS客户端的头像存储服务
    # 参数:
    #     client: 同步HTTP或OSS客户端
    # 返回:注入测试OSS客户端的AvatarStore实例
    value = object.__new__(AvatarStore)
    value.client = client
    value.bucket = "test-bucket"
    return value


@pytest.mark.asyncio
async def test_invalid_image_bytes_are_rejected_before_fixed_write() -> None:
    # 功能:验证头像图片字节无效时不写入固定头像对象
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    client = Client(b"forged png bytes")
    with pytest.raises(AppError) as failure:
        await store(client).confirm("user", "uploads/avatars/user/photo.png")
    assert failure.value.code == "AVATAR_IMAGE_INVALID"
    assert client.closed and not client.writes


@pytest.mark.asyncio
@pytest.mark.parametrize("collision", [False, True])
async def test_verified_avatar_is_normalized_and_fixed_with_safe_retries(collision: bool) -> None:
    # 功能:验证合法头像经过标准化固定并可安全重试
    # 参数:
    #     collision: 是否在头像固定写入测试中模拟对象键冲突
    # 返回:无返回值;断言失败时由pytest报告测试失败
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
