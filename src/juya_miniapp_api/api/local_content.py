# ruff: noqa: RUF001, RUF002
"""本地开发的发布快照，结构与正式内容接口一致，禁止用于生产数据。"""

from typing import Any

REVISION_ID = "01JLOCAL000000000000000001"


def published_scene(scene_id: str, legacy: dict[str, Any]) -> dict[str, Any]:
    # 功能:将旧本地场景转换为固定发布版本的内容契约
    # 参数:
    #     scene_id: 需要授权、学习或查询的场景公开标识
    #     legacy: 待转换为发布契约的旧本地场景数据
    # 返回:发布修订标识、内容版本及场景正文对象
    """将指定场景和历史演示数据转换为固定版本的本地发布响应。"""
    if scene_id == "scene-weekend-trip":
        return {
            "access": "PREVIEW",
            "sources": [],
            "activated_at": None,
            "earliest_expires_at": None,
            "authorization_pending": False,
            "scene": {
                "public_id": scene_id,
                "title": "周末公路旅行",
                "title_en": "A Weekend Road Trip",
                "title_zh": "周末公路旅行",
                "series": "旅行英语",
                "cover_url": "/static/home/coffee-home.png",
                "introduction": "可查看主题、难度与简介",
                "preview_status": "PREVIEW",
            },
        }
    coffee = scene_id == "scene-coffee-shop"
    old = legacy["scene"]
    audio_id = "coffee-audio" if coffee else "castle-audio"
    texts = (
        [
            ("Ivy", "Could I get a latte, please?", "我可以要一杯拿铁咖啡吗？"),
            ("Barista", "Sure. What size would you like?", "当然，你想要什么杯型？"),
            ("Ivy", "A medium, please.", "请来一杯中杯。"),
            ("Barista", "For here or to go?", "在这里喝还是带走？"),
            ("Ivy", "To go, please.", "请打包带走。"),
        ]
        if coffee
        else [
            (row.get("speaker", ""), row["text"], row.get("chinese", ""))
            for row in old["entries"]
            if row["entry_type"] == "DIALOGUE"
        ]
    )
    dialogue = [
        {
            "id": f"sentence-{index + 1}",
            "speaker": speaker,
            "english": english,
            "chinese": chinese,
            "start_ms": index * 5000,
            "end_ms": (index + 1) * 5000,
            "audio_version_id": "audio-v1",
            "timing_confirmed": True,
            "clickable_spans": [],
        }
        for index, (speaker, english, chinese) in enumerate(texts)
    ]
    if coffee:
        vocabulary = [
            {
                "entry_id": "word-latte",
                "entry_version": 1,
                "english": "latte",
                "phonetic": "/ˈlɑː.teɪ/",
                "chinese": "拿铁咖啡",
                "explanation": "espresso 加蒸奶制成的咖啡",
                "source_sentence_ids": ["sentence-1"],
                "audio_target_id": "latte-audio",
                "audio_version_id": "word-v1",
            },
            {
                "entry_id": "word-medium",
                "entry_version": 1,
                "english": "medium",
                "phonetic": "/ˈmiː.di.əm/",
                "chinese": "中杯；中等的",
                "explanation": "咖啡杯型中的中杯",
                "source_sentence_ids": ["sentence-3"],
                "audio_target_id": "medium-audio",
                "audio_version_id": "word-v1",
            },
            {
                "entry_id": "word-to-go",
                "entry_version": 1,
                "english": "to go",
                "phonetic": "",
                "chinese": "打包带走",
                "explanation": "点餐时说明打包带走",
                "source_sentence_ids": ["sentence-5"],
                "audio_target_id": None,
                "audio_version_id": None,
            },
        ]
        chunks = [
            {
                "entry_id": "phrase-could-i-get",
                "entry_version": 1,
                "english": "Could I get a latte, please?",
                "chinese": "我可以要……吗？",
                "explanation": "礼貌地点餐或提出请求",
                "source_sentence_ids": ["sentence-1"],
                "audio_target_id": None,
                "audio_version_id": None,
            },
            {
                "entry_id": "phrase-for-here",
                "entry_version": 1,
                "english": "For here or to go?",
                "chinese": "在这里喝还是打包带走？",
                "explanation": "询问堂食或打包",
                "source_sentence_ids": ["sentence-4"],
                "audio_target_id": None,
                "audio_version_id": None,
            },
            {
                "entry_id": "phrase-to-go",
                "entry_version": 1,
                "english": "To go, please.",
                "chinese": "打包带走，谢谢。",
                "explanation": "礼貌地说明打包",
                "source_sentence_ids": ["sentence-5"],
                "audio_target_id": None,
                "audio_version_id": None,
            },
        ]
        dialogue[0]["clickable_spans"] = [
            {
                "start": 14,
                "end": 19,
                "entry_id": "word-latte",
                "entry_version": 1,
                "source_locator": "sentence:sentence-1:entry:word-latte",
            },
            {
                "start": 0,
                "end": 11,
                "entry_id": "phrase-could-i-get",
                "entry_version": 1,
                "source_locator": "sentence:sentence-1:entry:phrase-could-i-get",
            },
        ]
        for sentence_index, entry_id, start, end in (
            (2, "word-medium", 2, 8),
            (4, "word-to-go", 0, 5),
            (3, "phrase-for-here", 0, 18),
            (4, "phrase-to-go", 0, 14),
        ):
            dialogue[sentence_index]["clickable_spans"].append(
                {
                    "start": start,
                    "end": end,
                    "entry_id": entry_id,
                    "entry_version": 1,
                    "source_locator": f"sentence:sentence-{sentence_index + 1}:entry:{entry_id}",
                }
            )
    else:
        vocabulary, chunks = [], []
        for row in old["entries"]:
            if row["entry_type"] == "DIALOGUE":
                continue
            entry = {
                "entry_id": row["entry_id"],
                "entry_version": 1,
                "english": row["text"],
                "phonetic": row.get("phonetic", ""),
                "chinese": row.get("chinese", ""),
                "explanation": row.get("explanation", ""),
                "source_sentence_ids": [row["source_locator"]],
                "audio_target_id": (row.get("audio") or {}).get("target_id"),
                "audio_version_id": (row.get("audio") or {}).get("version_id"),
            }
            (chunks if row["entry_type"] == "PHRASE" else vocabulary).append(entry)
            for sentence in dialogue:
                offset = sentence["english"].find(row["text"])
                if offset >= 0:
                    sentence["clickable_spans"].append(
                        {
                            "start": offset,
                            "end": offset + len(row["text"]),
                            "entry_id": row["entry_id"],
                            "entry_version": 1,
                            "source_locator": f"sentence:{sentence['id']}:entry:{row['entry_id']}",
                        }
                    )
    content = {
        "title_en": "At the Coffee Shop" if coffee else old["title"],
        "title_zh": "在咖啡店" if coffee else old["chinese_title"],
        "summary": "点一杯喜欢的咖啡，享受美好的时光。" if coffee else "讨论城堡展览",
        "tags": ["日常英语"],
        "original_image_asset_id": "coffee-original",
        "cover_asset_id": "coffee-cover",
        "copyright": "Figma 本地演示素材",
        "source": "Figma 2479:419",
        "audio": {
            "target_id": audio_id,
            "version_id": "audio-v1",
            "asset_id": audio_id,
            "duration_ms": 138000,
        },
        "dialogue": dialogue,
        "vocabulary": vocabulary,
        "chunks": chunks,
    }
    return {
        "access": "OPEN",
        "activated_at": None,
        "authorization_pending": False,
        "earliest_expires_at": None,
        "sources": ["OPEN"],
        "scene": {
            "scene_id": scene_id,
            "revision_id": REVISION_ID,
            "content_version": 1,
            "content": content,
        },
    }
