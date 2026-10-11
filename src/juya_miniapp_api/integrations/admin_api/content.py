"""Shared V1.3 draft and published content contract. All draft fields may be incomplete."""

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AudioReference(ContentModel):
    target_id: str
    version_id: str
    asset_id: str
    duration_ms: int = Field(ge=0)


class ClickableSpan(ContentModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    entry_id: str
    entry_version: int = Field(ge=1)
    source_locator: str


class DialogueSentence(ContentModel):
    # 匿名函数: 为新对话句子生成稳定标识的默认值。
    # 参数: 无。
    # 返回: UUID 的十六进制字符串。
    id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=64)
    speaker: str = Field(default="", max_length=100)
    english: str = Field(default="", max_length=10000)
    chinese: str = Field(default="", max_length=10000)
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    audio_version_id: str | None = None
    timing_confirmed: bool = False
    boundary_verified_by: str = ""
    slice_target_id: str | None = None
    slice_version: str | None = None
    slice_dependency_hash: str | None = None
    clickable_spans: list[ClickableSpan] = Field(default_factory=list)


class SceneEntry(ContentModel):
    entry_id: str = ""
    entry_version: int = Field(default=1, ge=1)
    english: str = Field(default="", max_length=10000)
    variants: list[str] = Field(default_factory=list, max_length=100)
    phonetic: str = Field(default="", max_length=200)
    chinese: str = Field(default="", max_length=10000)
    explanation: str = Field(default="", max_length=10000)
    source_sentence_ids: list[str] = Field(default_factory=list)
    icon_asset_id: str | None = None
    audio_target_id: str | None = None
    audio_version_id: str | None = None
    sense_id: str = ""
    source_id: str = ""
    source_sentence: str = Field(default="", max_length=10000)
    source_locator: dict[str, str] | None = None
    pronunciation_target_id: str | None = None
    pronunciation_text: str = Field(default="", max_length=10000)
    reading_variant: str = Field(default="", max_length=255)
    allow_synthesis: bool = False
    confirmed: bool = False
    meaning_change: Literal["wording", "new_sense"] | None = None


class SceneContent(ContentModel):
    schema_version: Literal[1, 2] = 1
    template_type: Literal["dialogue", "word_card", "vocabulary"] = "dialogue"
    title_en: str = Field(default="", max_length=200)
    title_zh: str = Field(default="", max_length=200)
    summary: str = Field(default="", max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=100)
    original_image_asset_id: str | None = None
    cover_asset_id: str | None = None
    copyright: str = Field(default="", max_length=5000)
    source: str = Field(default="", max_length=5000)
    audio: AudioReference | None = None
    dialogue: list[DialogueSentence] = Field(default_factory=list, max_length=1000)
    vocabulary: list[SceneEntry] = Field(default_factory=list, max_length=1000)
    chunks: list[SceneEntry] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def unique_objects(self) -> "SceneContent":
        # 功能:验证对话标识及词汇、语块英文拼写在各组内不重复。
        # 参数:
        #     self: 当前 SceneContent 实例,持有本方法访问的依赖和业务状态。
        # 返回:通过唯一性校验的场景内容对象。
        ids = [row.id for row in self.dialogue]
        if len(ids) != len(set(ids)):
            raise ValueError("Dialogue sentence identifiers must be unique")
        for entries in (self.vocabulary, self.chunks):
            if self.schema_version == 2:
                entry_ids = [entry.entry_id for entry in entries if entry.entry_id]
                if len(entry_ids) != len(set(entry_ids)):
                    raise ValueError("本篇条目标识不能重复")
                continue
            spellings = [
                entry.english.strip().casefold() for entry in entries if entry.english.strip()
            ]
            if len(spellings) != len(set(spellings)):
                raise ValueError("Reuse one dictionary entry for repeated source sentences")
        if self.schema_version == 2 and any(entry.icon_asset_id for entry in self.chunks):
            raise ValueError("语块不能配置图片")
        return self
