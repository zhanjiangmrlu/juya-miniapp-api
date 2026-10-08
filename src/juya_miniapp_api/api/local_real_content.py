"""Only explicit local previews may serve a management snapshot and its exact source files."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import FileResponse

from juya_miniapp_api.integrations.admin_api.schemas import PublishedScene


class LocalRealContent:
    """Pin the selected management revision and validate all referenced media before serving it."""

    def __init__(self, manifest_path: Path) -> None:
        """manifest_path is the non-secret snapshot exported from the local management database"""
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.scene = PublishedScene.model_validate(manifest["scene"]).model_dump(mode="json")
        self.scene_id = str(self.scene["scene_id"])
        self.revision_id = str(self.scene["revision_id"])
        content = self.scene["content"]
        self.resources: dict[str, dict[str, Any]] = {}
        for resource_id in (content["original_image_asset_id"], content["cover_asset_id"]):
            if resource_id:
                self.resources[resource_id] = manifest["media"][resource_id]
        audio = content["audio"]
        if not audio:
            raise ValueError("Management revision has no audio")
        self.resources[audio["target_id"]] = manifest["media"][audio["asset_id"]]
        for entry in content["vocabulary"] + content["chunks"]:
            target = entry["audio_target_id"]
            if target:
                self.resources[target] = manifest["media"][target]
        for media in self.resources.values():
            path = Path(media["path"])
            if not path.is_file():
                raise ValueError("Referenced management media is missing")
            if hashlib.sha256(path.read_bytes()).hexdigest() != media["sha256"]:
                raise ValueError("Referenced management media hash mismatch")
            media["stat"] = (path.stat().st_size, path.stat().st_mtime_ns)

    def opened(self, scene_id: str) -> dict[str, Any]:
        """Return a copy of the pinned revision for the requested scene_id"""
        if scene_id != self.scene_id:
            raise HTTPException(404, "Scene not found")
        return {
            "access": "OPEN",
            "sources": ["OPEN"],
            "scene": deepcopy(self.scene),
            "authorization_pending": False,
            "activated_at": None,
            "earliest_expires_at": None,
        }

    def catalog(self, base_url: str, progress: int) -> dict[str, Any]:
        """Build the local learning catalog using base_url for media and current progress"""
        content = self.scene["content"]
        cover = content["cover_asset_id"] or content["original_image_asset_id"]
        return {
            "authorization_pending": False,
            "items": [
                {
                    "scene_id": self.scene_id,
                    "title": content["title_en"],
                    "chinese_title": content["title_zh"],
                    "series": "真实内容预览",
                    "summary": content["summary"],
                    "tags": content["tags"],
                    "image_url": f"{base_url}/local-dev/resources/{cover}" if cover else "",
                    "access": "OPEN",
                    "sources": ["OPEN"],
                    "progress": progress,
                    "trial_sentence": content["dialogue"][0]["english"]
                    if content["dialogue"]
                    else "",
                }
            ],
        }

    def validate_resource(self, scene_id: str, resource_id: str, revision_id: str) -> None:
        """Authorize resource_id only for the selected scene_id and pinned revision_id"""
        self.opened(scene_id)
        if revision_id != self.revision_id:
            raise HTTPException(409, "Scene revision changed")
        if resource_id not in self.resources:
            raise HTTPException(404, "Resource not referenced")

    def resource(self, resource_id: str) -> FileResponse:
        """Serve the exact pinned file for resource_id, retaining HTTP Range and HEAD support"""
        media = self.resources.get(resource_id)
        if not media:
            raise HTTPException(404, "Resource not referenced")
        path = Path(media["path"])
        if not path.is_file():
            raise HTTPException(404, "Media unavailable")
        if (path.stat().st_size, path.stat().st_mtime_ns) != media["stat"]:
            raise HTTPException(409, "Media changed; restart the local preview")
        return FileResponse(
            path, media_type=media["content_type"], headers={"Cache-Control": "private, no-store"}
        )
