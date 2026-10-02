# ruff: noqa: RUF001, RUF002

import io
import wave
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse

from juya_miniapp_api.api.local_content import REVISION_ID, published_scene


def _catalog() -> dict[str, Any]:
    """创建可独立修改的本地学习目录。"""
    return {
        "authorization_pending": False,
        "items": [
            {
                "access": "OPEN",
                "chinese_title": "讨论城堡展览",
                "image_url": "/static/images/castle-card.png",
                "progress": 68,
                "scene_id": "scene-castle",
                "series": "日常英语",
                "tags": ["开放学习场景"],
                "title": "Discussing the Castle Exhibit",
                "trial_sentence": "What do you think of the castle exhibit?",
            },
            {
                "access": "OPEN",
                "chinese_title": "点早餐",
                "image_url": "/static/images/castle-card.png",
                "progress": 0,
                "scene_id": "scene-breakfast",
                "series": "日常英语",
                "tags": ["开放学习场景"],
                "title": "Ordering Breakfast",
                "trial_sentence": "I'd like some breakfast, please.",
            },
            {
                "access": "OPEN",
                "chinese_title": "在咖啡店",
                "image_url": "/static/images/castle-card.png",
                "progress": 32,
                "scene_id": "scene-coffee-shop",
                "series": "日常英语",
                "tags": ["开放学习场景"],
                "title": "At the Coffee Shop",
                "trial_sentence": "Could I get a latte, please?",
            },
            {
                "access": "PREVIEW",
                "chinese_title": "周末公路旅行",
                "description": "可查看主题、难度与简介",
                "image_url": "/static/images/castle-card.png",
                "scene_id": "scene-weekend-trip",
                "series": "旅行英语",
                "tags": ["内容预览"],
                "title": "A Weekend Road Trip",
            },
        ],
        "profile_completion_enabled": True,
    }


def _scene() -> dict[str, Any]:
    """创建本地场景详情契约。"""
    return {
        "access": "OPEN",
        "activated_at": None,
        "authorization_pending": False,
        "earliest_expires_at": None,
        "scene": {
            "access": "OPEN",
            "chinese_title": "城堡展览讨论会",
            "entries": [
                {
                    "audio": {
                        "target_id": "audio-sentence-1",
                        "target_type": "sentence",
                        "version_id": "v1",
                    },
                    "chinese": "你觉得他们为什么举办这个展览？",
                    "entry_id": "sentence-1",
                    "entry_type": "DIALOGUE",
                    "source_locator": "sentence-1",
                    "speaker": "Ivy",
                    "text": "Why do you think they put together this exhibit?",
                },
                {
                    "audio": {
                        "target_id": "audio-sentence-2",
                        "target_type": "sentence",
                        "version_id": "v1",
                    },
                    "chinese": "这有助于人们理解城堡是如何逐渐发展的。",
                    "entry_id": "sentence-2",
                    "entry_type": "DIALOGUE",
                    "source_locator": "sentence-2",
                    "speaker": "Noah",
                    "text": "It helps people understand how the castle evolved.",
                },
                {
                    "audio": {
                        "target_id": "audio-evolved",
                        "target_type": "word",
                        "version_id": "v1",
                    },
                    "chinese": "演变；逐渐发展",
                    "entry_id": "word-evolved",
                    "entry_type": "VOCABULARY",
                    "explanation": "用于描述事物逐步变化。",
                    "phonetic": "/ɪ'vɒlvd/",
                    "source_locator": "sentence-2",
                    "text": "evolved",
                },
                {
                    "audio": {
                        "target_id": "audio-put-together",
                        "target_type": "phrase",
                        "version_id": "v1",
                    },
                    "chinese": "组织；组合",
                    "entry_id": "phrase-put-together",
                    "entry_type": "PHRASE",
                    "explanation": "在此处表示组织或举办。",
                    "source_locator": "sentence-1",
                    "text": "put together",
                },
            ],
            "hero_image_url": "/static/images/castle-card.png",
            "image_url": "/static/images/castle-card.png",
            "scene_id": "scene-castle",
            "series": "日常英语",
            "tags": ["开放学习场景"],
            "title": "Discussing the Castle Exhibit",
        },
        "sources": ["OPEN"],
    }


def _favorite() -> dict[str, Any]:
    """创建默认收藏条目。"""
    return {
        "entry_stable_id": "word-evolved",
        "entry_type": "VOCABULARY",
        "favorited_at": "2026-09-28T08:30:00Z",
        "id": "favorite-evolved",
        "last_reviewed_at": None,
        "normalized_key": "evolved",
        "sources": [
            {
                "original_link": "/scenes/scene-castle#sentence-2",
                "scene_id": "scene-castle",
                "sentence_snapshot": "The castle evolved.",
                "source_locator": "sentence-2",
            }
        ],
    }


def _feedback() -> dict[str, Any]:
    """创建默认反馈条目。"""
    return {
        "category": "CONTENT",
        "created_at": "2026-09-28T08:30:00Z",
        "description": "Castle Exhibit 第二句的中文释义似乎不准确。",
        "id": "feedback-001",
        "reopen_count": 0,
        "reply": "已核对并修正释义，感谢你的反馈。",
        "resolved_at": "2026-09-28T10:30:00Z",
        "screenshots": [],
        "status": "RESOLVED",
        "supplements": [],
        "title": "Castle Exhibit 第二句释义",
    }


def _message() -> dict[str, Any]:
    """创建默认站内消息。"""
    return {
        "created_at": "2026-09-28T10:30:00Z",
        "id": "message-1",
        "read_at": None,
        "related_id": "feedback-001",
        "related_type": "FEEDBACK",
        "summary": "Castle Exhibit 第二句释义已核对处理。",
        "title": "你的问题已有处理结果",
        "type": "SYSTEM",
    }


def _silent_wav(seconds: int = 1) -> bytes:
    """生成一秒静音 WAV，供本地音频播放链路联调。"""
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8_000)
        audio.writeframes(b"\x00\x00" * 8_000 * seconds)
    return output.getvalue()


class LocalDevState:
    """保存单个本地开发应用实例的可变演示数据。"""

    def __init__(self) -> None:
        self.catalog = _catalog()
        self.prompt_exposures: set[str] = set()
        self.deletion: dict[str, Any] | None = None
        self.contact: dict[str, Any] | None = {
            "can_self_edit": True,
            "change_pending": False,
            "consent_version": "v1",
            "contact_status": "CONTACTED",
            "self_edit_count": 0,
            "wechat_id": "juya_english",
        }
        self.favorites = {"favorite-evolved": _favorite()}
        default_feedback = _feedback()
        self.feedback = {default_feedback["id"]: default_feedback}
        default_message = _message()
        self.messages = {default_message["id"]: default_message}


def create_local_dev_router() -> APIRouter:
    """创建仅限本地环境使用的完整小程序契约路由。"""
    router = APIRouter()
    state = LocalDevState()

    @router.post("/api/v1/session/wechat")
    async def login_with_wechat() -> dict[str, str]:
        """跳过真实微信 code2session 并返回固定本地令牌。"""
        return {
            "access_token": "local-access-token",
            "refresh_token": "local-refresh-token",
        }

    @router.post("/api/v1/session/refresh")
    async def refresh_session() -> dict[str, str]:
        """刷新本地令牌以覆盖客户端自动续期流程。"""
        return {
            "access_token": "local-access-token",
            "refresh_token": "local-refresh-token",
        }

    @router.get("/api/v1/home")
    async def get_home() -> dict[str, Any]:
        """返回首页聚合数据。"""
        unread_count = sum(message["read_at"] is None for message in state.messages.values())
        return {
            "checkins": {"current_streak": 12, "longest_streak": 18, "total_days": 36},
            "greeting": "下午好",
            "today_task": {
                "card_ids": [],
                "kind": "CONTINUE_SCENE",
                "target_id": "scene-castle",
            },
            "unread_message_count": unread_count,
        }

    @router.get("/api/v1/learning/modules")
    async def get_modules() -> dict[str, Any]:
        """返回本地启用的学习模块。"""
        return {
            "items": [
                {
                    "enabled": True,
                    "key": "scene_learning",
                    "public_id": "module-scene-learning",
                    "title": "场景学习",
                }
            ]
        }

    @router.get("/api/v1/learning/catalog")
    async def get_catalog(request: Request) -> dict[str, Any]:
        """返回当前本地学习目录快照。"""
        catalog = deepcopy(state.catalog)
        for item in catalog["items"]:
            item["image_url"] = (
                f"{str(request.base_url).rstrip('/')}/local-dev/resources/coffee-cover"
            )
        return catalog

    @router.get("/api/v1/me")
    async def get_profile() -> dict[str, Any]:
        """返回本地用户资料。"""
        return {
            "avatar_url": None,
            "juya_id": "JY-LOCAL-0001",
            "nickname": "本地小芽",
            "wechat_nickname": None,
            "deletion": deepcopy(state.deletion),
            "contact_prompt_eligible": (
                state.contact is None
                and not state.prompt_exposures
                and sum(
                    item.get("access") == "OPEN" and item.get("progress") == 100
                    for item in state.catalog["items"]
                )
                >= 3
            ),
        }

    @router.get("/api/v1/me/contact")
    async def get_contact() -> dict[str, Any] | None:
        """返回本地联系资料。"""
        return deepcopy(state.contact)

    @router.put("/api/v1/me/contact")
    async def save_contact(request: Request) -> dict[str, Any]:
        """保存本地联系资料并递增自助修改次数。"""
        payload = await request.json()
        edit_count = int((state.contact or {}).get("self_edit_count", 0))
        state.contact = {
            "can_self_edit": edit_count < 1,
            "change_pending": False,
            "consent_version": payload.get("consent_version", "v1"),
            "contact_status": "CONTACTED",
            "self_edit_count": edit_count + 1,
            "wechat_id": payload.get("wechat_id", ""),
        }
        return deepcopy(state.contact)

    @router.delete("/api/v1/me/contact")
    async def remove_contact() -> None:
        """删除本地联系资料。"""
        state.contact = None

    @router.post("/api/v1/me/contact/corrections")
    async def create_contact_correction() -> dict[str, Any]:
        """创建本地联系资料更正申请。"""
        return {
            "created_at": "2026-09-28T08:30:00Z",
            "id": "local-correction",
            "status": "PENDING",
        }

    @router.post("/api/v1/me/contact/prompt-exposures")
    async def prompt_exposure(request: Request) -> dict[str, bool]:
        """按实际请求幂等键登记本地提示曝光。"""
        key = request.headers.get("Idempotency-Key") or request.headers.get("X-Idempotency-Key")
        if not key:
            raise HTTPException(422, "Idempotency-Key required")
        created = not state.prompt_exposures
        state.prompt_exposures.add(key)
        return {"created": created}

    @router.get("/api/v1/me/entitlements")
    async def get_entitlements() -> dict[str, Any]:
        """返回正式权益与限时权益。"""
        return {
            "authorization_pending": False,
            "formal": [
                {
                    "content_pack_id": "pack-1",
                    "effective_at": "2026-09-01T00:00:00Z",
                    "expires_at": None,
                    "id": "formal-1",
                    "status": "ACTIVE",
                    "title": "正式内容包",
                }
            ],
            "limited": [
                {
                    "activated_at": None,
                    "activity_id": "activity-1",
                    "duration_days": 3,
                    "expires_at": None,
                    "id": "limited-1",
                    "scene_count": 3,
                    "starts_before": "2026-10-01T08:00:00Z",
                    "status": "PENDING",
                    "title": "3 天限时学习",
                }
            ],
            "version": "v1",
        }

    @router.post("/api/v1/scenes/{scene_id}/open")
    async def open_scene(scene_id: str) -> dict[str, Any]:
        """返回指定场景，本地数据统一复用完整示例内容。"""
        return published_scene(scene_id, _scene())

    @router.get("/api/v1/scenes/{scene_id}/entries/{entry_id}")
    async def get_entry(
        scene_id: str, entry_id: str, revision_id: str, entry_version: int, source_locator: str
    ) -> dict[str, Any]:
        """读取固定修订、词条版本与来源对应的本地词卡。"""
        opened = published_scene(scene_id, _scene())
        if opened["access"] == "PREVIEW":
            raise HTTPException(403, "Scene access denied")
        if revision_id != REVISION_ID:
            raise HTTPException(409, "Scene revision changed")
        content = opened["scene"]["content"]
        for entry_type, rows in (
            ("VOCABULARY", content["vocabulary"]),
            ("PHRASE", content["chunks"]),
        ):
            for entry in rows:
                if entry["entry_id"] != entry_id or entry["entry_version"] != entry_version:
                    continue
                locators = {f"{'chunks' if entry_type == 'PHRASE' else 'vocabulary'}:{entry_id}"}
                locators.update(
                    f"sentence:{sid}:entry:{entry_id}" for sid in entry["source_sentence_ids"]
                )
                if source_locator not in locators:
                    raise HTTPException(404, "Source not found")
                snapshot = "\n".join(
                    row["english"]
                    for row in content["dialogue"]
                    if row["id"] in entry["source_sentence_ids"]
                )
                return {
                    **entry,
                    "scene_id": scene_id,
                    "revision_id": revision_id,
                    "source_locator": source_locator,
                    "entry_type": entry_type,
                    "sentence_snapshot": snapshot or entry["english"],
                }
        raise HTTPException(404, "Entry not found")

    @router.get("/api/v1/scenes/{scene_id}/resources/{resource_id}/signed-url")
    async def sign_resource(
        scene_id: str, resource_id: str, revision_id: str, request: Request, response: Response
    ) -> dict[str, str]:
        """只给当前场景固定修订实际引用的资源返回本地地址。"""
        opened = published_scene(scene_id, _scene())
        if opened["access"] == "PREVIEW":
            raise HTTPException(403, "Scene access denied")
        if revision_id != REVISION_ID:
            raise HTTPException(409, "Scene revision changed")
        content = opened["scene"]["content"]
        refs = {
            content["original_image_asset_id"],
            content["cover_asset_id"],
            content["audio"]["target_id"],
        }
        refs.update(
            entry.get("audio_target_id") for entry in content["vocabulary"] + content["chunks"]
        )
        if resource_id not in refs:
            raise HTTPException(404, "Resource not referenced")
        response.headers["Cache-Control"] = "private, no-store"
        base = str(request.base_url).rstrip("/")
        return {
            "resource_id": resource_id,
            "expires_at": "2099-01-01T00:00:00Z",
            "url": f"{base}/local-dev/resources/{resource_id}",
        }

    @router.get("/local-dev/resources/{resource_id}")
    def resource_bytes(resource_id: str) -> Response:
        """提供本地演示原图或合成静音音频，不能作为真机试听证据。"""
        if resource_id in {"coffee-original", "coffee-cover"}:
            asset = Path(__file__).resolve().parents[3] / "fixtures" / "coffee-original.png"
            if not asset.is_file():
                raise HTTPException(404, "Local image unavailable")
            return FileResponse(asset, media_type="image/png")
        if resource_id in {
            "coffee-audio",
            "castle-audio",
            "latte-audio",
            "audio-evolved",
            "audio-put-together",
        }:
            return Response(_silent_wav(138), media_type="audio/wav")
        raise HTTPException(404, "Resource not found")

    @router.put("/api/v1/scenes/{scene_id}/progress")
    async def save_progress(scene_id: str, request: Request) -> dict[str, Any]:
        """回显并保存页面提交的场景进度位置。"""
        payload = await request.json()
        for item in state.catalog["items"]:
            if item["scene_id"] == scene_id:
                item["progress"] = max(int(item.get("progress", 0)), 1)
        return {**payload, "scene_id": scene_id}

    @router.post("/api/v1/scenes/{scene_id}/complete")
    async def complete_scene(scene_id: str) -> dict[str, Any]:
        """完成场景并更新本地目录进度。"""
        for item in state.catalog["items"]:
            if item["scene_id"] == scene_id:
                item["progress"] = 100
        return {"checkin_date": "2026-09-28", "created": True, "progress": {}}

    @router.get("/api/v1/scenes/{scene_id}/result")
    async def get_scene_result(scene_id: str) -> dict[str, int]:
        """返回场景完成后的本地学习统计。"""
        _ = scene_id
        return {
            "completed_scenes": 1,
            "favorite_phrases": 3,
            "favorite_vocabulary": 6,
            "streak_days": 12,
        }

    @router.post("/api/v1/media/{target_id}/signed-url")
    async def get_signed_media(target_id: str, request: Request) -> dict[str, str]:
        """返回指向当前本地服务的静音音频地址。"""
        base_url = str(request.base_url).rstrip("/")
        return {
            "expires_at": "2099-01-01T00:00:00Z",
            "target_id": target_id,
            "url": f"{base_url}/local-dev/media/{target_id}.wav",
        }

    @router.get("/local-dev/media/{target_id}.wav")
    async def get_local_audio(target_id: str) -> Response:
        """提供静音音频响应以验证本地播放器状态机。"""
        _ = target_id
        return Response(_silent_wav(), media_type="audio/wav")

    @router.get("/api/v1/favorites")
    async def list_favorites() -> dict[str, Any]:
        """返回本地收藏分页。"""
        return {
            "has_more": False,
            "items": deepcopy(list(state.favorites.values())),
            "next_cursor": None,
        }

    @router.post("/api/v1/favorites")
    async def create_favorite(request: Request) -> dict[str, Any]:
        """创建并保存本地收藏。"""
        payload = await request.json()
        favorite_id = f"local-favorite-{len(state.favorites) + 1}"
        item = {**payload, "id": favorite_id}
        state.favorites[favorite_id] = item
        return deepcopy(item)

    @router.get("/api/v1/favorites/{favorite_id}")
    async def get_favorite(favorite_id: str) -> dict[str, Any]:
        """返回指定收藏，不存在时回退到默认示例。"""
        return deepcopy(state.favorites.get(favorite_id, _favorite()))

    @router.delete("/api/v1/favorites/{favorite_id}")
    async def remove_favorite(favorite_id: str) -> None:
        """删除指定本地收藏。"""
        state.favorites.pop(favorite_id, None)

    @router.get("/api/v1/history/scenes")
    async def get_scene_history() -> dict[str, Any]:
        """返回本地场景学习历史。"""
        return {
            "items": [
                {
                    "completed_at": "2026-09-28T08:40:00Z",
                    "last_learned_at": "2026-09-28T08:40:00Z",
                    "scene_id": "scene-castle",
                }
            ]
        }

    @router.post("/api/v1/reviews")
    async def create_review() -> dict[str, Any]:
        """创建本地复习会话。"""
        return {"card_count": 1, "id": "local-review", "started_at": "2026-09-28T08:30:00Z"}

    @router.post("/api/v1/reviews/{review_id}/complete")
    async def complete_review(review_id: str) -> dict[str, Any]:
        """完成本地复习会话。"""
        _ = review_id
        return {"completed_at": "2026-09-28T08:40:00Z", "created": True}

    @router.get("/api/v1/messages")
    async def list_messages() -> dict[str, Any]:
        """返回本地站内消息分页。"""
        return {"items": deepcopy(list(state.messages.values())), "next_cursor": None}

    @router.post("/api/v1/messages/{message_id}/read")
    async def mark_message_read(message_id: str) -> dict[str, Any]:
        """标记指定本地消息为已读。"""
        message = state.messages.get(message_id, _message())
        message["read_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        state.messages[message_id] = message
        return deepcopy(message)

    @router.post("/api/v1/feedback/uploads")
    async def create_upload_credential(request: Request) -> dict[str, Any]:
        """返回由当前本地服务接收的反馈图片上传凭据。"""
        base_url = str(request.base_url).rstrip("/")
        return {
            "access_key_id": "local-access-key",
            "content_type": request.query_params.get("content_type", "image/jpeg"),
            "expires_at": "2099-01-01T00:00:00Z",
            "host": f"{base_url}/local-dev/uploads",
            "key": "feedback/local-user/local-image.jpg",
            "max_bytes": 5_242_880,
            "policy": "local-policy",
            "signature": "local-signature",
            "fields": {
                "key": "feedback/local-user/local-image.jpg",
                "Content-Type": request.query_params.get("content_type", "image/jpeg"),
                "policy": "local-policy",
                "x-oss-signature-version": "OSS4-HMAC-SHA256",
                "x-oss-signature": "local-signature",
            },
        }

    @router.post("/local-dev/uploads")
    async def accept_local_upload(request: Request) -> Response:
        """接收并丢弃本地图片内容，避免依赖真实 OSS。"""
        await request.body()
        return Response(status_code=204)

    @router.get("/api/v1/feedback")
    async def list_feedback() -> dict[str, Any]:
        """返回本地反馈列表。"""
        return {"has_more": False, "items": deepcopy(list(state.feedback.values()))}

    @router.post("/api/v1/feedback")
    async def create_feedback(request: Request) -> dict[str, Any]:
        """创建本地反馈并保留页面提交内容。"""
        payload = await request.json()
        feedback_id = f"local-feedback-{len(state.feedback) + 1}"
        item = {
            **_feedback(),
            **payload,
            "id": feedback_id,
            "reply": None,
            "resolved_at": None,
            "status": "PENDING",
            "supplements": [],
        }
        state.feedback[feedback_id] = item
        return deepcopy(item)

    @router.get("/api/v1/feedback/{feedback_id}")
    async def get_feedback(feedback_id: str) -> dict[str, Any]:
        """返回指定本地反馈。"""
        return deepcopy(state.feedback.get(feedback_id, _feedback()))

    @router.post("/api/v1/feedback/{feedback_id}/supplements")
    async def supplement_feedback(feedback_id: str, request: Request) -> dict[str, Any]:
        """追加本地反馈补充说明。"""
        payload = await request.json()
        item = state.feedback.get(feedback_id, _feedback())
        images = payload.get("screenshots", [])
        if len(images) > 1 or (images and item.get("screenshots")):
            raise HTTPException(422, "Each feedback accepts one screenshot")
        item["screenshots"] = item.get("screenshots", []) + images
        item["status"] = "USER_SUPPLIED"
        item.setdefault("supplements", []).append(
            {
                "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "text": payload.get("text", ""),
            }
        )
        state.feedback[feedback_id] = item
        return deepcopy(item)

    @router.post("/api/v1/feedback/{feedback_id}/resolution")
    async def resolve_feedback(feedback_id: str, request: Request) -> dict[str, Any]:
        """确认或重新打开本地反馈。"""
        payload = await request.json()
        item = state.feedback.get(feedback_id, _feedback())
        reopened = payload.get("action") == "REOPEN"
        item["reopen_count"] = 1 if reopened else item.get("reopen_count", 0)
        item["status"] = "REOPENED" if reopened else "RESOLVED"
        state.feedback[feedback_id] = item
        return deepcopy(item)

    @router.delete("/api/v1/me/learning-data", status_code=204)
    async def clear_learning_data() -> None:
        """清理本地学习进度、收藏和消息已读状态。"""
        for item in state.catalog["items"]:
            item["progress"] = 0
        state.favorites.clear()

    @router.post("/api/v1/me/deletion")
    async def request_deletion() -> dict[str, Any]:
        """返回本地账号注销等待期。"""
        if state.deletion and state.deletion["status"] == "PENDING":
            return deepcopy(state.deletion)
        now = datetime.now(UTC)
        state.deletion = {
            "completed_at": None,
            "effective_at": (now + timedelta(days=7)).isoformat(),
            "id": "local-deletion",
            "requested_at": now.isoformat(),
            "revoked_at": None,
            "status": "PENDING",
        }
        return deepcopy(state.deletion)

    @router.post("/api/v1/me/deletion/revoke")
    async def revoke_deletion() -> dict[str, Any]:
        """撤回本地账号注销申请。"""
        if not state.deletion:
            raise HTTPException(409, "No pending deletion")
        state.deletion["status"] = "REVOKED"
        state.deletion["revoked_at"] = datetime.now(UTC).isoformat()
        return deepcopy(state.deletion)

    return router
