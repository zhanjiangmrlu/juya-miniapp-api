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
from juya_miniapp_api.api.local_real_content import LocalRealContent
from juya_miniapp_api.modules.favorites.router import FavoriteRequest
from juya_miniapp_api.modules.favorites.service import normalize_favorite_key

_FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"


def _catalog() -> dict[str, Any]:
    # 功能:构造本地开发的学习目录示例数据
    # 参数:
    #     无形参。
    # 返回:本地学习模块、目录项目、用户摘要和访问权限
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
    # 功能:构造本地开发的场景正文示例
    # 参数:
    #     无形参。
    # 返回:本地示例场景的标题、对话、单词、短语及音频信息
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
    # 功能:构造本地开发的默认收藏示例
    # 参数:
    #     无形参。
    # 返回:收藏类型、标准化英文、学习时间与固定版本来源列表
    """从本地发布词卡创建带可定位来源的默认收藏"""
    entry = _local_entry(
        "scene-castle", "word-evolved", REVISION_ID, 1, "sentence:sentence-2:entry:word-evolved"
    )
    return _favorite_item(entry, "favorite-evolved")


def _favorite_item(entry: dict[str, Any], favorite_id: str) -> dict[str, Any]:
    # 功能:将已解析词条组装为本地收藏及来源快照
    # 参数:
    #     entry: 已从发布内容解析出的权威词条数据
    #     favorite_id: 当前用户收藏记录的公开标识
    # 返回:收藏标识、词条快照与固定版本来源信息
    """将已解析发布词卡 entry 保存为指定 favorite_id 的收藏契约"""
    return {
        "entry_stable_id": entry["entry_id"],
        "entry_type": entry["entry_type"],
        "favorited_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "id": favorite_id,
        "last_reviewed_at": None,
        "normalized_key": normalize_favorite_key(entry["english"]),
        "sources": [
            {
                "original_link": f"/scenes/{entry['scene_id']}",
                "scene_id": entry["scene_id"],
                "sentence_snapshot": entry["sentence_snapshot"],
                "source_locator": entry["source_locator"],
                "revision_id": entry["revision_id"],
                "entry_version": entry["entry_version"],
                "entry_snapshot": deepcopy(entry),
            }
        ],
    }


def _local_entry(
    scene_id: str,
    entry_id: str,
    revision_id: str,
    entry_version: int,
    source_locator: str,
    opened: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # 功能:校验本地发布版本和来源定位并解析词条
    # 参数:
    #     scene_id: 需要授权、学习或查询的场景公开标识
    #     entry_id: 场景中的词条稳定标识
    #     revision_id: 需要访问或固定的场景发布修订标识
    #     entry_version: 固定词条的内容版本号
    #     source_locator: 词条来源在固定场景版本中的定位片段
    #     opened: 已授权打开的本地场景内容与发布版本数据
    # 返回:固定修订与词条版本对应的英文、来源语句及发音信息
    """解析场景 scene_id 中词条 entry_id 的固定修订 revision_id 与版本 entry_version

    source_locator 为正文来源，opened 为可选真实管理快照
    """
    if opened is None:
        if not any(item["scene_id"] == scene_id for item in _catalog()["items"]):
            raise HTTPException(404, "Scene not found")
        opened = published_scene(scene_id, _scene())
    if opened["access"] == "PREVIEW":
        raise HTTPException(403, "Scene access denied")
    if revision_id != opened["scene"]["revision_id"]:
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


def _feedback() -> dict[str, Any]:
    # 功能:构造本地开发的默认反馈示例
    # 参数:
    #     无形参。
    # 返回:本地反馈标识、问题说明、处理状态与对话记录
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
    # 功能:构造本地开发的默认站内消息示例
    # 参数:
    #     无形参。
    # 返回:站内消息标识、标题、摘要、关联对象和已读时间
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
    # 功能:生成指定时长的静音WAV供本地音频调试
    # 参数:
    #     seconds: 本地静音WAV的持续秒数
    # 返回:指定时长的WAV音频字节
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
        # 功能:初始化小程序的LocalDevState对象的状态存储
        # 参数:
        #     self: 当前小程序的LocalDevState实例
        # 返回:无返回值。
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


def create_local_dev_router(real_content: LocalRealContent | None = None) -> APIRouter:
    # 功能:创建并绑定本地开发路由与业务依赖
    # 参数:
    #     real_content: 可选的本地真实发布内容与媒体加载器
    # 返回:包含业务端点的FastAPI路由器
    """创建本地契约路由，real_content 为显式指定的真实管理端修订与素材"""
    router = APIRouter()
    state = LocalDevState()
    if real_content:
        state.catalog = real_content.catalog("", 0)
        state.favorites.clear()
    real_history: dict[str, dict[str, Any]] = {}

    def record_real_history(scene_id: str, completed: bool = False) -> None:
        # 功能:在本地真实内容模式记录场景开始与完成时间
        # 参数:
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     completed: 本次场景历史更新是否标记学习完成
        # 返回:无返回值。
        """为真实场景 scene_id 保存本地学习历史，completed 表示本次完成场景"""
        if not real_content:
            return
        real_content.opened(scene_id)
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        item = real_history.setdefault(scene_id, {"scene_id": scene_id, "completed_at": None})
        item["last_learned_at"] = now
        if completed:
            item["completed_at"] = now

    @router.post("/api/v1/session/wechat")
    async def login_with_wechat() -> dict[str, str]:
        # 功能:返回本地开发固定的访问和刷新凭证
        # 参数:
        #     无形参。
        # 返回:会话标识、用户信息、访问凭证与刷新凭证
        """跳过真实微信 code2session 并返回固定本地令牌。"""
        return {
            "access_token": "local-access-token",
            "refresh_token": "local-refresh-token",
        }

    @router.post("/api/v1/session/refresh")
    async def refresh_session() -> dict[str, str]:
        # 功能:返回本地开发固定的访问和刷新凭证
        # 参数:
        #     无形参。
        # 返回:轮换后的会话标识、访问凭证与刷新凭证
        """刷新本地令牌以覆盖客户端自动续期流程。"""
        return {
            "access_token": "local-access-token",
            "refresh_token": "local-refresh-token",
        }

    @router.get("/api/v1/home")
    async def get_home() -> dict[str, Any]:
        # 功能:返回本地首页问候、打卡、今日任务与未读消息数量
        # 参数:
        #     无形参。
        # 返回:首页问候、打卡统计、今日任务和未读消息数量
        """返回首页聚合数据。"""
        unread_count = sum(message["read_at"] is None for message in state.messages.values())
        return {
            "checkins": {"current_streak": 12, "longest_streak": 18, "total_days": 36},
            "greeting": "下午好",
            "today_task": {
                "card_ids": [],
                "kind": "CONTINUE_SCENE",
                "target_id": real_content.scene_id if real_content else "scene-castle",
            },
            "unread_message_count": unread_count,
        }

    @router.get("/api/v1/learning/modules")
    async def get_modules() -> dict[str, Any]:
        # 功能:获取学习模块定义与目录版本信息
        # 参数:
        #     无形参。
        # 返回:可用学习模块定义列表
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
        # 功能:获取带用户摘要和授权信息的学习目录
        # 参数:
        #     request: FastAPI请求对象
        # 返回:学习目录项目、摘要与场景权限投影
        """返回当前本地学习目录快照。"""
        if real_content:
            return real_content.catalog(
                str(request.base_url).rstrip("/"), state.catalog["items"][0]["progress"]
            )
        catalog = deepcopy(state.catalog)
        for item in catalog["items"]:
            item["image_url"] = (
                f"{str(request.base_url).rstrip('/')}/local-dev/resources/coffee-cover"
            )
        return catalog

    @router.get("/api/v1/me")
    async def get_profile() -> dict[str, Any]:
        # 功能:读取用户账号与昵称头像资料
        # 参数:
        #     无形参。
        # 返回:用户公开标识、昵称、头像与联系方式状态
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
        # 功能:读取当前用户联系方式状态
        # 参数:
        #     无形参。
        # 返回:联系方式业务状态、修改资格和核验标记;原记录不存在时为None
        """返回本地联系资料。"""
        return deepcopy(state.contact)

    @router.put("/api/v1/me/contact")
    async def save_contact(request: Request) -> dict[str, Any]:
        # 功能:在本地开发状态中保存联系方式并限制真实变更次数与纠错状态
        # 参数:
        #     request: FastAPI请求对象
        # 返回:保存后的联系方式状态、修改资格与纠错信息
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
        # 功能:撤回本地联系方式并清理敏感内容
        # 参数:
        #     无形参。
        # 返回:无返回值。
        """删除本地联系资料。"""
        state.contact = None

    @router.post("/api/v1/me/contact/corrections")
    async def create_contact_correction() -> dict[str, Any]:
        # 功能:创建本地联系方式纠错申请
        # 参数:
        #     无形参。
        # 返回:新建纠错申请的公开标识与处理状态
        """创建本地联系资料更正申请。"""
        return {
            "created_at": "2026-09-28T08:30:00Z",
            "id": "local-correction",
            "status": "PENDING",
        }

    @router.post("/api/v1/me/contact/prompt-exposures")
    async def prompt_exposure(request: Request) -> dict[str, bool]:
        # 功能:幂等记录当前用户联系方式引导的首次曝光
        # 参数:
        #     request: FastAPI请求对象
        # 返回:created字段,标记是否首次创建联系方式引导曝光
        """按实际请求幂等键登记本地提示曝光。"""
        key = request.headers.get("Idempotency-Key") or request.headers.get("X-Idempotency-Key")
        if not key:
            raise HTTPException(422, "Idempotency-Key required")
        created = not state.prompt_exposures
        state.prompt_exposures.add(key)
        return {"created": created}

    @router.get("/api/v1/me/entitlements")
    async def get_entitlements() -> dict[str, Any]:
        # 功能:获取用户当前授权权益投影
        # 参数:
        #     无形参。
        # 返回:当前用户授权权益、场景访问级别和到期时间
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
        # 功能:在本地开发状态中校验场景访问响应并仅在授权成功后记录打开历史
        # 参数:
        #     scene_id: 需要授权、学习或查询的场景公开标识
        # 返回:场景访问级别、权限信息与固定发布版本内容
        """返回 scene_id 对应的真实修订或隔离演示内容"""
        if real_content:
            return real_content.opened(scene_id)
        return published_scene(scene_id, _scene())

    @router.get("/api/v1/scenes/{scene_id}/entries/{entry_id}")
    async def get_entry(
        scene_id: str, entry_id: str, revision_id: str, entry_version: int, source_locator: str
    ) -> dict[str, Any]:
        # 功能:按发布版本与来源定位查询权威词条内容
        # 参数:
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     entry_id: 场景中的词条稳定标识
        #     revision_id: 需要访问或固定的场景发布修订标识
        #     entry_version: 固定词条的内容版本号
        #     source_locator: 词条来源在固定场景版本中的定位片段
        # 返回:固定版本词条的英文、释义、来源语句与发音信息
        """读取场景 scene_id 中词条 entry_id 的修订 revision_id 与版本 entry_version

        source_locator 为正文来源
        """
        return _local_entry(
            scene_id,
            entry_id,
            revision_id,
            entry_version,
            source_locator,
            real_content.opened(scene_id) if real_content else None,
        )

    @router.get("/api/v1/scenes/{scene_id}/resources/{resource_id}/signed-url")
    async def sign_resource(
        scene_id: str, resource_id: str, revision_id: str, request: Request, response: Response
    ) -> dict[str, str]:
        # 功能:为本地固定发布版本资源生成访问地址
        # 参数:
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     resource_id: 发布内容中的媒体资源标识
        #     revision_id: 需要访问或固定的场景发布修订标识
        #     request: FastAPI请求对象
        #     response: HTTP响应对象
        # 返回:资源访问URL与有效期
        """为场景 scene_id、资源 resource_id 和修订 revision_id 返回本地资源地址

        request 提供服务地址，response 用于设置缓存策略
        """
        if real_content:
            real_content.validate_resource(scene_id, resource_id, revision_id)
            response.headers["Cache-Control"] = "private, no-store"
            return {
                "resource_id": resource_id,
                "expires_at": "2099-01-01T00:00:00Z",
                "url": f"{str(request.base_url).rstrip('/')}/local-dev/resources/{resource_id}",
            }
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

    @router.api_route("/local-dev/resources/{resource_id}", methods=["GET", "HEAD"])
    def resource_bytes(resource_id: str) -> Response:
        # 功能:返回本地内容资源的原始媒体字节
        # 参数:
        #     resource_id: 发布内容中的媒体资源标识
        # 返回:HTTP响应对象
        """按 resource_id 返回真实素材；未启用真实预览时提供隔离演示夹具"""
        if real_content:
            return real_content.resource(resource_id)
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
            asset = _FIXTURES / "silence-138.wav"
            return FileResponse(asset, media_type="audio/wav")
        raise HTTPException(404, "Resource not found")

    @router.put("/api/v1/scenes/{scene_id}/progress")
    async def save_progress(scene_id: str, request: Request) -> dict[str, Any]:
        # 功能:按客户端序号保存场景阅读位置并拒绝旧请求覆盖
        # 参数:
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     request: FastAPI请求对象
        # 返回:场景阅读位置、客户端序号及学习时间
        """按场景 scene_id 保存 request 提交的进度并更新本地学习历史"""
        payload = await request.json()
        record_real_history(scene_id)
        for item in state.catalog["items"]:
            if item["scene_id"] == scene_id:
                item["progress"] = max(int(item.get("progress", 0)), 1)
        return {**payload, "scene_id": scene_id}

    @router.post("/api/v1/scenes/{scene_id}/complete")
    async def complete_scene(scene_id: str) -> dict[str, Any]:
        # 功能:完成本地场景学习并更新历史与打卡状态
        # 参数:
        #     scene_id: 需要授权、学习或查询的场景公开标识
        # 返回:完成后的学习进度、首次完成标记和打卡日期
        """完成场景 scene_id 并更新本地目录及历史"""
        record_real_history(scene_id, completed=True)
        for item in state.catalog["items"]:
            if item["scene_id"] == scene_id:
                item["progress"] = 100
        return {"checkin_date": "2026-09-28", "created": True, "progress": {}}

    @router.get("/api/v1/scenes/{scene_id}/result")
    async def get_scene_result(scene_id: str) -> dict[str, int]:
        # 功能:返回本地场景学习完成统计
        # 参数:
        #     scene_id: 需要授权、学习或查询的场景公开标识
        # 返回:已完成场景、连续学习天数和收藏数量等成就
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
        # 功能:获取授权有效期内的媒体访问签名链接
        # 参数:
        #     target_id: 需要学习或签发媒体链接的目标公开标识
        #     request: FastAPI请求对象
        # 返回:可访问的媒体URL与签名到期时间
        """按 target_id 返回 request 所在服务的音频资源地址"""
        if real_content:
            real_content.validate_resource(
                real_content.scene_id, target_id, real_content.revision_id
            )
            return {
                "target_id": target_id,
                "expires_at": "2099-01-01T00:00:00Z",
                "url": f"{str(request.base_url).rstrip('/')}/local-dev/resources/{target_id}",
            }
        base_url = str(request.base_url).rstrip("/")
        return {
            "expires_at": "2099-01-01T00:00:00Z",
            "target_id": target_id,
            "url": f"{base_url}/local-dev/media/{target_id}.wav",
        }

    @router.api_route("/local-dev/media/{target_id}.wav", methods=["GET", "HEAD"])
    async def get_local_audio(target_id: str) -> Response:
        # 功能:返回本地场景音频并支持浏览器分段读取
        # 参数:
        #     target_id: 需要学习或签发媒体链接的目标公开标识
        # 返回:HTTP响应对象
        """按 target_id 提供真实录音，隔离模式继续使用测试夹具"""
        if real_content:
            return real_content.resource(target_id)
        _ = target_id
        asset = _FIXTURES / "silence-1.wav"
        return FileResponse(asset, media_type="audio/wav")

    @router.get("/api/v1/favorites")
    async def list_favorites() -> dict[str, Any]:
        # 功能:按游标分页查询当前用户收藏
        # 参数:
        #     无形参。
        # 返回:收藏列表、下一页游标与是否存在后续页的标记
        """返回本地收藏分页。"""
        return {
            "has_more": False,
            "items": deepcopy(list(state.favorites.values())),
            "next_cursor": None,
        }

    @router.post("/api/v1/favorites")
    async def create_favorite(payload: FavoriteRequest) -> dict[str, Any]:
        # 功能:在本地开发状态中创建收藏并固定发布版本、词条内容和来源快照
        # 参数:
        #     payload: 已校验的收藏词条类别、发布版本与来源定位
        # 返回:新建或合并后的收藏记录与固定版本来源快照
        """按正式请求 payload 解析发布来源并保存本地收藏"""
        entry = _local_entry(
            payload.scene_id,
            payload.entry_stable_id,
            payload.revision_id,
            payload.entry_version,
            payload.source_locator,
            real_content.opened(payload.scene_id) if real_content else None,
        )
        if payload.entry_type != entry["entry_type"]:
            raise HTTPException(422, "Entry type does not match published entry")
        favorite_id = f"local-favorite-{len(state.favorites) + 1}"
        item = _favorite_item(entry, favorite_id)
        state.favorites[favorite_id] = item
        return deepcopy(item)

    @router.get("/api/v1/favorites/{favorite_id}")
    async def get_favorite(favorite_id: str) -> dict[str, Any]:
        # 功能:查询当前用户收藏详情
        # 参数:
        #     favorite_id: 当前用户收藏记录的公开标识
        # 返回:指定收藏记录与固定版本来源快照
        """读取 favorite_id 对应收藏，真实模式的失效收藏返回未找到"""
        if real_content and favorite_id not in state.favorites:
            raise HTTPException(404, "Favorite not found")
        return deepcopy(state.favorites.get(favorite_id, _favorite()))

    @router.delete("/api/v1/favorites/{favorite_id}")
    async def remove_favorite(favorite_id: str) -> None:
        # 功能:删除本地收藏及关联来源数据
        # 参数:
        #     favorite_id: 当前用户收藏记录的公开标识
        # 返回:无返回值。
        """删除指定本地收藏。"""
        state.favorites.pop(favorite_id, None)

    @router.get("/api/v1/history/scenes")
    async def get_scene_history() -> dict[str, Any]:
        # 功能:返回本地已打开场景的学习历史
        # 参数:
        #     无形参。
        # 返回:已打开场景的学习历史列表与分页标记
        """返回本地场景学习历史。"""
        if real_content:
            return {"items": deepcopy(list(real_history.values()))}
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
        # 功能:在本地开发状态中校验并固定用户选择的收藏卡片生成复习会话
        # 参数:
        #     无形参。
        # 返回:复习会话标识、类型、开始时间、卡片数量与固定卡片标识
        """创建本地复习会话。"""
        return {"card_count": 1, "id": "local-review", "started_at": "2026-09-28T08:30:00Z"}

    @router.post("/api/v1/reviews/{review_id}/complete")
    async def complete_review(review_id: str) -> dict[str, Any]:
        # 功能:在本地开发状态中幂等完成复习并更新所选收藏的复习时间与打卡
        # 参数:
        #     review_id: 收藏复习会话的公开标识
        # 返回:复习会话标识、首次完成标记与北京时间打卡日期
        """完成本地复习会话。"""
        _ = review_id
        return {"completed_at": "2026-09-28T08:40:00Z", "created": True}

    @router.get("/api/v1/messages")
    async def list_messages() -> dict[str, Any]:
        # 功能:按游标分页查询用户站内消息
        # 参数:
        #     无形参。
        # 返回:站内消息列表、游标与未读消息数量
        """返回本地站内消息分页。"""
        return {"items": deepcopy(list(state.messages.values())), "next_cursor": None}

    @router.post("/api/v1/messages/{message_id}/read")
    async def mark_message_read(message_id: str) -> dict[str, Any]:
        # 功能:将本地站内消息标记为已读
        # 参数:
        #     message_id: 用户站内消息的公开标识
        # 返回:已标记读取时间的站内消息字段
        """标记指定本地消息为已读。"""
        message = state.messages.get(message_id, _message())
        message["read_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        state.messages[message_id] = message
        return deepcopy(message)

    @router.post("/api/v1/feedback/uploads")
    async def create_upload_credential(request: Request) -> dict[str, Any]:
        # 功能:签发本地调试的反馈截图上传凭证
        # 参数:
        #     request: FastAPI请求对象
        # 返回:上传地址、对象键、表单字段与凭证到期时间
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
        # 功能:接收本地调试上传并返回上传成功状态
        # 参数:
        #     request: FastAPI请求对象
        # 返回:HTTP响应对象
        """接收并丢弃本地图片内容，避免依赖真实 OSS。"""
        await request.body()
        return Response(status_code=204)

    @router.get("/api/v1/feedback")
    async def list_feedback() -> dict[str, Any]:
        # 功能:列出当前用户提交的反馈
        # 参数:
        #     无形参。
        # 返回:当前用户反馈列表与分页标记
        """返回本地反馈列表。"""
        return {"has_more": False, "items": deepcopy(list(state.feedback.values()))}

    @router.post("/api/v1/feedback")
    async def create_feedback(request: Request) -> dict[str, Any]:
        # 功能:提交当前用户反馈及来源和截图对象键
        # 参数:
        #     request: FastAPI请求对象
        # 返回:新建反馈记录及其处理状态
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
        # 功能:获取反馈记录及处理进度
        # 参数:
        #     feedback_id: 用户反馈记录的公开标识
        # 返回:反馈标识、问题说明、截图对象键和处理进度
        """返回指定本地反馈。"""
        return deepcopy(state.feedback.get(feedback_id, _feedback()))

    @router.post("/api/v1/feedback/{feedback_id}/supplements")
    async def supplement_feedback(feedback_id: str, request: Request) -> dict[str, Any]:
        # 功能:在本地开发状态中向管理端提交反馈补充文本与截图
        # 参数:
        #     feedback_id: 用户反馈记录的公开标识
        #     request: FastAPI请求对象
        # 返回:补充文本与截图后更新的反馈记录
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
        # 功能:在本地开发状态中将反馈处理结果确认或异议发送至管理端
        # 参数:
        #     feedback_id: 用户反馈记录的公开标识
        #     request: FastAPI请求对象
        # 返回:用户确认或提出异议后更新的反馈记录
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
        # 功能:在本地开发状态中清空用户学习记录、收藏、复习和打卡数据
        # 参数:
        #     无形参。
        # 返回:无返回值。
        """清理本地学习进度、收藏和消息已读状态。"""
        for item in state.catalog["items"]:
            item["progress"] = 0
        state.favorites.clear()

    @router.post("/api/v1/me/deletion")
    async def request_deletion() -> dict[str, Any]:
        # 功能:在本地开发状态中幂等创建账号注销申请并设置七天等待期
        # 参数:
        #     无形参。
        # 返回:注销申请标识、等待状态与生效时间
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
        # 功能:在本地开发状态中撤回等待期内的账号注销申请
        # 参数:
        #     无形参。
        # 返回:已撤回的注销申请状态与原生效时间
        """撤回本地账号注销申请。"""
        if not state.deletion:
            raise HTTPException(409, "No pending deletion")
        state.deletion["status"] = "REVOKED"
        state.deletion["revoked_at"] = datetime.now(UTC).isoformat()
        return deepcopy(state.deletion)

    return router
