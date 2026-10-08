import pytest
from httpx import ASGITransport, AsyncClient

from juya_miniapp_api.api.local_content import REVISION_ID
from juya_miniapp_api.infrastructure.config import Settings
from juya_miniapp_api.main import create_app


async def assert_source_opens(client: AsyncClient, favorite: dict) -> None:
    # 功能:在测试中校验收藏来源链接可打开匹配发布版本的场景
    # 参数:
    #     client: 异步HTTP客户端
    #     favorite: 待验证来源或序列化的收藏数据
    # 返回:无返回值。
    """核对 client 返回的收藏 favorite 可定位且与词卡快照一致"""
    source = favorite["sources"][0]
    entry = await client.get(
        f"/api/v1/scenes/{source['scene_id']}/entries/{favorite['entry_stable_id']}",
        params={
            "revision_id": source["revision_id"],
            "entry_version": source["entry_version"],
            "source_locator": source["source_locator"],
        },
    )
    assert entry.status_code == 200
    assert source["entry_snapshot"] == entry.json()
    assert source["sentence_snapshot"] == entry.json()["sentence_snapshot"]
    assert favorite["normalized_key"] == entry.json()["english"].lower()


@pytest.mark.asyncio
async def test_default_favorite_has_reachable_published_source() -> None:
    # 功能:验证默认收藏具有可访问的发布来源
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    """默认收藏必须携带能够返回原文的版本和完整来源"""
    app = create_app(Settings(environment="local", local_dev_mode=True))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        favorite = (await client.get("/api/v1/favorites/favorite-evolved")).json()
        await assert_source_opens(client, favorite)


@pytest.mark.asyncio
async def test_created_favorite_persists_resolved_source_snapshot() -> None:
    # 功能:验证新收藏保存已解析的来源内容快照
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    """页面按正式请求创建收藏后列表和详情必须保留可用来源"""
    app = create_app(Settings(environment="local", local_dev_mode=True))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/v1/favorites",
            json={
                "entry_type": "VOCABULARY",
                "text": "latte",
                "entry_stable_id": "word-latte",
                "revision_id": REVISION_ID,
                "entry_version": 1,
                "scene_id": "scene-coffee-shop",
                "source_locator": "sentence:sentence-1:entry:word-latte",
            },
        )
        assert created.status_code == 200
        favorite = created.json()
        await assert_source_opens(client, favorite)
        detail = (await client.get(f"/api/v1/favorites/{favorite['id']}")).json()
        listed = (await client.get("/api/v1/favorites")).json()["items"]
        assert detail == favorite
        assert favorite in listed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "expected_status"),
    [
        ({"revision_id": "old"}, 409),
        ({"entry_version": 2}, 404),
        ({"source_locator": "sentence:unknown:entry:word-latte"}, 404),
        ({"scene_id": "scene-weekend-trip"}, 403),
        (
            {
                "scene_id": "does-not-exist",
                "entry_stable_id": "word-evolved",
                "source_locator": "sentence:sentence-2:entry:word-evolved",
            },
            404,
        ),
        ({"entry_type": "PHRASE"}, 422),
        ({"entry_type": "INVALID"}, 422),
    ],
)
async def test_created_favorite_rejects_unresolvable_source(
    changes: dict, expected_status: int
) -> None:
    # 功能:验证新收藏拒绝不能解析的来源
    # 参数:
    #     changes: 测试覆盖的配置字段变更映射
    #     expected_status: 测试请求预期得到的HTTP状态码
    # 返回:无返回值;断言失败时由pytest报告测试失败
    """收藏请求 changes 中无效的发布来源返回 expected_status 且不写入收藏"""
    app = create_app(Settings(environment="local", local_dev_mode=True))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        before = (await client.get("/api/v1/favorites")).json()
        response = await client.post(
            "/api/v1/favorites",
            json={
                "entry_type": "VOCABULARY",
                "text": "latte",
                "entry_stable_id": "word-latte",
                "revision_id": REVISION_ID,
                "entry_version": 1,
                "scene_id": "scene-coffee-shop",
                "source_locator": "sentence:sentence-1:entry:word-latte",
                **changes,
            },
        )
        assert response.status_code == expected_status
        assert (await client.get("/api/v1/favorites")).json() == before
