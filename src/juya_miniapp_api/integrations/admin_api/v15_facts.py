"""Read-only final access facts mirrored from admin V1.5 SQLAuthorizer.

The supplied BFF transaction holds locks until snapshot/progress write commits.
Keep the projection aligned through actual admin/BFF integration tests.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from juya_miniapp_api.integrations.admin_api.v15_client import ContentType
from juya_miniapp_api.modules.favorites.snapshots import request_digest


@dataclass(frozen=True)
class AccessFacts:
    full_access: bool
    visible: bool
    module_version: int
    authorization_version: str
    expires_at: datetime | None = None


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def details_hash(value: object) -> str:
    return request_digest(value)


class SQLAuthorizer:
    async def __call__(
        self,
        session: AsyncSession,
        user_id: str,
        kind: ContentType,
        identity: str,
        now: datetime,
        *,
        lock: bool,
    ) -> AccessFacts:
        suffix = " FOR SHARE" if lock else ""
        module = "short_reading" if kind == "short_reading" else "scene_learning"
        enabled = await session.scalar(
            text("SELECT enabled FROM learning_module_config WHERE module_type=:module" + suffix),
            {"module": module},
        )
        config = await session.scalar(
            text("SELECT version FROM discovery_config_state WHERE id=1" + suffix)
        )
        user = (
            (
                await session.execute(
                    text(
                        "SELECT id,status,updated_at FROM user_account WHERE public_id=:id" + suffix
                    ),
                    {"id": user_id},
                )
            )
            .mappings()
            .first()
        )
        module_version = int(config or 0)
        if user is None or user["status"] != "ACTIVE" or not enabled:
            return AccessFacts(False, False, module_version, "DISABLED")
        table = "short_reading" if kind == "short_reading" else "scene"
        row = (
            (
                await session.execute(
                    text(
                        f"SELECT id,status,published_revision_id FROM {table} "
                        "WHERE public_id=:id" + suffix
                    ),
                    {"id": identity},
                )
            )
            .mappings()
            .first()
        )
        if row is None or row["status"] != "PUBLISHED" or row["published_revision_id"] is None:
            return AccessFacts(False, False, module_version, "UNPUBLISHED")
        params = {"user": user["id"], "kind": kind, "identity": identity, "scene": row["id"]}
        package_rows = (
            (
                await session.execute(
                    text(
                        "SELECT p.id,p.status FROM content_package p "
                        "JOIN content_package_item i ON i.package_id=p.id "
                        "WHERE i.content_type=:kind AND i.content_id=:identity ORDER BY p.id"
                        + suffix
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        if kind != "short_reading":
            legacy = (
                (
                    await session.execute(
                        text(
                            "SELECT p.id,p.status FROM content_package p "
                            "JOIN content_package_scene i ON i.package_id=p.id "
                            "WHERE i.scene_id=:scene ORDER BY p.id" + suffix
                        ),
                        params,
                    )
                )
                .mappings()
                .all()
            )
            package_rows = [*package_rows, *legacy]
        formal: list[dict[str, Any]] = []
        for package in sorted({p["id"]: p for p in package_rows}.values(), key=lambda p: p["id"]):
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT public_id,status,version,expires_at "
                            "FROM formal_entitlement WHERE user_id=:user AND package_id=:package "
                            "ORDER BY id" + suffix
                        ),
                        {**params, "package": package["id"]},
                    )
                )
                .mappings()
                .all()
            )
            if package["status"] == "ACTIVE":
                formal.extend(
                    dict(r)
                    for r in rows
                    if r["status"] == "ACTIVE"
                    and (r["expires_at"] is None or now < utc(r["expires_at"]))
                )
        opened = False
        visible = False
        limited: list[dict[str, Any]] = []
        trial_version = 0
        if kind == "short_reading":
            trial = (
                (
                    await session.execute(
                        text(
                            "SELECT enabled,version FROM short_reading_trial "
                            "WHERE short_reading_id=:identity" + suffix
                        ),
                        params,
                    )
                )
                .mappings()
                .first()
            )
            opened = bool(trial and trial["enabled"])
            trial_version = int(trial["version"]) if trial else 0
            visible = (
                True  # Independent short-reading safe preview exists for every published item.
            )
        else:
            current_open = (
                (
                    await session.execute(
                        text(
                            "SELECT id,version FROM open_scene_config "
                            "ORDER BY version DESC LIMIT 1" + suffix
                        )
                    )
                )
                .mappings()
                .first()
            )
            if current_open:
                opened = bool(
                    await session.scalar(
                        text(
                            "SELECT id FROM open_scene_item "
                            "WHERE config_id=:config AND scene_id=:scene" + suffix
                        ),
                        {**params, "config": current_open["id"]},
                    )
                )
                trial_version = int(current_open["version"])
            visible = bool(
                await session.scalar(
                    text("SELECT enabled FROM preview_config WHERE scene_id=:scene" + suffix),
                    params,
                )
            )
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT le.public_id,le.status,le.version,le.expires_at "
                            "FROM limited_entitlement le JOIN limited_campaign_scene lcs "
                            "ON lcs.campaign_version_id=le.campaign_version_id "
                            "WHERE le.user_id=:user AND lcs.scene_id=:scene ORDER BY le.id" + suffix
                        ),
                        params,
                    )
                )
                .mappings()
                .all()
            )
            limited = [
                dict(r)
                for r in rows
                if r["status"] == "ACTIVE" and r["expires_at"] and now < utc(r["expires_at"])
            ]
        grants = [*formal, *limited]
        expiries = [utc(r["expires_at"]) for r in grants if r["expires_at"] is not None]
        expires_at = (
            None
            if opened or any(r["expires_at"] is None for r in grants)
            else (max(expiries) if expiries else None)
        )
        full = opened or bool(grants)
        authorization_version = details_hash(
            {
                "user": str(user["updated_at"]),
                "packages": [dict(p) for p in package_rows],
                "trial": trial_version,
                "grants": [{**g, "expires_at": str(g["expires_at"])} for g in grants],
            }
        )
        return AccessFacts(full, full or visible, module_version, authorization_version, expires_at)
