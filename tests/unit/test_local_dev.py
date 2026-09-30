import pytest
from httpx import ASGITransport, AsyncClient

from juya_miniapp_api.infrastructure.config import Settings
from juya_miniapp_api.main import create_app


@pytest.mark.asyncio
async def test_local_dev_mode_exposes_bootstrap_and_learning_contracts() -> None:
    """本地模式应在无外部基础设施时提供小程序启动和学习所需契约。"""
    app = create_app(Settings(environment="local", local_dev_mode=True))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post("/api/v1/session/wechat", json={"code": "local-code"})
        modules = await client.get("/api/v1/learning/modules")
        home = await client.get("/api/v1/home")
        scene = await client.post("/api/v1/scenes/scene-castle/open")
        contact = await client.get("/api/v1/me/contact")
        upload = await client.post("/api/v1/feedback/uploads?content_type=image/png")

    assert login.status_code == 200
    assert login.json() == {
        "access_token": "local-access-token",
        "refresh_token": "local-refresh-token",
    }
    assert modules.json()["items"][0]["key"] == "scene_learning"
    assert home.json()["today_task"]["target_id"] == "scene-castle"
    assert scene.json()["scene"]["scene_id"] == "scene-castle"
    assert contact.json()["contact_status"] == "CONTACTED"
    assert upload.json()["fields"]["Content-Type"] == "image/png"


@pytest.mark.asyncio
async def test_local_dev_mode_preserves_mutable_feedback_and_message_state() -> None:
    """本地模式的反馈与消息操作应返回可供页面继续流转的最新状态。"""
    app = create_app(Settings(environment="local", local_dev_mode=True))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/v1/feedback",
            json={
                "category": "CONTENT",
                "description": "本地联调反馈",
                "screenshots": [],
                "title": "本地反馈",
            },
        )
        supplemented = await client.post(
            f"/api/v1/feedback/{created.json()['id']}/supplements",
            json={"text": "补充说明"},
        )
        read_message = await client.post("/api/v1/messages/message-1/read")

    assert created.status_code == 200
    assert created.json()["description"] == "本地联调反馈"
    assert supplemented.json()["status"] == "SUPPLEMENTED"
    assert supplemented.json()["supplements"][-1]["text"] == "补充说明"
    assert read_message.json()["read_at"] is not None


@pytest.mark.asyncio
async def test_local_dev_mode_is_rejected_outside_local_environment() -> None:
    """生产环境不得因误设本地开关而暴露开发用接口。"""
    with pytest.raises(RuntimeError, match="OSS"):
        create_app(Settings(environment="production", local_dev_mode=True))
