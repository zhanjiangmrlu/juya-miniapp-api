import hashlib
import io
import json
import wave
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from juya_miniapp_api.infrastructure.config import Settings
from juya_miniapp_api.main import create_app


def preview_file(tmp_path: Path) -> tuple[Path, bytes]:
    """在 tmp_path 创建含有声 PCM、固定管理修订与素材哈希的预览清单"""
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x00\x10\x00\xf0" * 4000)
    data = output.getvalue()
    asset = tmp_path / "recording.wav"
    asset.write_bytes(data)
    scene = {
        "scene_id": "real-scene",
        "revision_id": "real-revision",
        "content_version": 2,
        "content": {
            "title_en": "A Better Way to Work",
            "title_zh": "更好的协作方式",
            "audio": {
                "target_id": "real-target",
                "version_id": "real-version",
                "asset_id": "real-asset",
                "duration_ms": 1000,
            },
            "dialogue": [
                {
                    "id": "sentence-1",
                    "english": "How can we solve this problem?",
                    "start_ms": 0,
                    "end_ms": 900,
                    "audio_version_id": "real-version",
                    "timing_confirmed": False,
                }
            ],
            "vocabulary": [
                {
                    "entry_id": "solve",
                    "entry_version": 2,
                    "english": "solve",
                    "source_sentence_ids": ["sentence-1"],
                }
            ],
        },
    }
    manifest = tmp_path / "preview.json"
    manifest.write_text(
        json.dumps(
            {
                "scene": scene,
                "media": {
                    "real-asset": {
                        "path": str(asset),
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "content_type": "audio/wav",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return manifest, data


@pytest.mark.asyncio
async def test_real_preview_replaces_demo_content_and_serves_exact_recording(
    tmp_path: Path,
) -> None:
    """真实预览应同步替换正文、目录、首页、词卡与收藏并提供真实音频"""
    manifest, data = preview_file(tmp_path)
    app = create_app(
        Settings(environment="local", local_dev_mode=True, local_content_file=manifest)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        home = (await client.get("/api/v1/home")).json()
        catalog = (await client.get("/api/v1/learning/catalog")).json()
        opened = (await client.post("/api/v1/scenes/real-scene/open")).json()
        assert home["today_task"]["target_id"] == "real-scene"
        assert [r["scene_id"] for r in catalog["items"]] == ["real-scene"]
        content = opened["scene"]["content"]
        assert content["title_zh"] == "更好的协作方式"
        assert content["audio"]["duration_ms"] == 1000
        assert content["dialogue"][0]["timing_confirmed"] is False
        signed = await client.get(
            "/api/v1/scenes/real-scene/resources/real-target/signed-url?revision_id=real-revision"
        )
        assert signed.status_code == 200
        url = signed.json()["url"]
        full = await client.get(url)
        partial = await client.get(url, headers={"Range": "bytes=100-199"})
        head = await client.head(url)
        assert full.content == data and any(full.content[44:])
        assert partial.status_code == 206 and partial.content == data[100:200]
        assert head.status_code == 200 and not head.content
        assert signed.headers["cache-control"] == "private, no-store"
        entry = await client.get(
            "/api/v1/scenes/real-scene/entries/solve?revision_id=real-revision"
            "&entry_version=2&source_locator=sentence:sentence-1:entry:solve"
        )
        assert entry.status_code == 200
        assert entry.json()["sentence_snapshot"] == "How can we solve this problem?"
        favorite = await client.post(
            "/api/v1/favorites",
            json={
                "scene_id": "real-scene",
                "entry_type": "VOCABULARY",
                "text": "solve",
                "entry_stable_id": "solve",
                "revision_id": "real-revision",
                "entry_version": 2,
                "source_locator": "sentence:sentence-1:entry:solve",
            },
        )
        assert favorite.status_code == 200
        assert favorite.json()["sources"][0]["revision_id"] == "real-revision"
        assert (await client.post("/api/v1/scenes/scene-castle/open")).status_code == 404
        assert (
            await client.get(
                "/api/v1/scenes/real-scene/resources/real-target/signed-url?revision_id=old"
            )
        ).status_code == 409
        assert (
            await client.get(
                "/api/v1/scenes/real-scene/resources/unknown/signed-url?revision_id=real-revision"
            )
        ).status_code == 404
        assert (await client.get("/local-dev/resources/castle-audio")).status_code == 404
        assert (await client.get("/local-dev/media/castle-audio.wav")).status_code == 404
        (tmp_path / "recording.wav").write_bytes(b"wrong recording")
        assert (await client.get(url)).status_code == 409


def test_real_preview_rejects_missing_or_mismatched_recording(tmp_path: Path) -> None:
    """清单素材缺失或哈希错误时启动失败并禁止使用另一份音频"""
    manifest, _ = preview_file(tmp_path)
    asset = tmp_path / "recording.wav"
    asset.write_bytes(b"wrong recording")
    with pytest.raises(ValueError, match="hash"):
        create_app(Settings(environment="local", local_dev_mode=True, local_content_file=manifest))
    asset.unlink()
    with pytest.raises(ValueError, match="missing"):
        create_app(Settings(environment="local", local_dev_mode=True, local_content_file=manifest))


def test_real_preview_cannot_be_enabled_outside_local_mode(tmp_path: Path) -> None:
    """管理端草稿预览不能在正式路由或非本地环境中开启"""
    manifest, _ = preview_file(tmp_path)
    with pytest.raises(RuntimeError, match="local"):
        create_app(Settings(environment="test", local_dev_mode=True, local_content_file=manifest))
    with pytest.raises(RuntimeError, match="local"):
        create_app(Settings(environment="local", local_dev_mode=False, local_content_file=manifest))


@pytest.mark.asyncio
async def test_real_preview_history_and_missing_favorites_never_use_demo_scene(
    tmp_path: Path,
) -> None:
    """真实模式的学习历史和失效收藏不能指向已被替换的城堡演示内容"""
    manifest, _ = preview_file(tmp_path)
    app = create_app(
        Settings(environment="local", local_dev_mode=True, local_content_file=manifest)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/v1/favorites/old-favorite")).status_code == 404
        assert (await client.get("/api/v1/history/scenes")).json() == {"items": []}
        assert (
            await client.put(
                "/api/v1/scenes/real-scene/progress",
                json={
                    "client_sequence": 1,
                    "entry_id": "sentence-1",
                    "offset": 0,
                },
            )
        ).status_code == 200
        rows = (await client.get("/api/v1/history/scenes")).json()["items"]
        assert len(rows) == 1 and rows[0]["scene_id"] == "real-scene"
        assert rows[0]["last_learned_at"] and rows[0]["completed_at"] is None
        await client.post("/api/v1/scenes/real-scene/complete")
        rows = (await client.get("/api/v1/history/scenes")).json()["items"]
        assert rows[0]["completed_at"]
