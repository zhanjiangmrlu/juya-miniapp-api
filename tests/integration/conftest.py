"""仅在显式隔离SQL测试内创建有效的发布/权益资料,清理仅限本fixture拥有的记录。"""

import json
from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import text

from juya_miniapp_api.shared.ids import new_ulid


class SceneFixtures:
    def __init__(self):
        # 功能:记录仅属于本fixture的SQL资料和原模块配置。
        self.owned = []

    async def create(self, sessions, user, scene, now, kind="dialogue"):
        # 功能:为指定隔离账号创建真实已发布场景及永久正式权益,不修改真实发布内容。
        identity = new_ulid(datetime.now(UTC))
        async with sessions() as session, session.begin():
            enabled = await session.scalar(
                text(
                    "SELECT enabled FROM learning_module_config WHERE module_type='scene_learning'"
                )
            )
            await session.execute(
                text(
                    "UPDATE learning_module_config SET enabled=1 WHERE module_type='scene_learning'"
                )
            )
            await session.execute(
                text(
                    "INSERT INTO content_series(public_id,slug,title,status) "
                    "VALUES(:id,:id,'private scene guard fixture','PUBLISHED')"
                ),
                {"id": identity},
            )
            series = await session.scalar(text("SELECT LAST_INSERT_ID()"))
            version = await session.scalar(
                text(
                    "SELECT COALESCE(MAX(version),0)+1 FROM content_template "
                    "WHERE template_type=:kind"
                ),
                {"kind": kind},
            )
            await session.execute(
                text(
                    "INSERT INTO content_template(template_type,version,required_modules,"
                    "validation_rules) "
                    "VALUES(:kind,:version,'[]','{}')"
                ),
                {"kind": kind, "version": version},
            )
            template = await session.scalar(text("SELECT LAST_INSERT_ID()"))
            await session.execute(
                text(
                    "INSERT INTO scene(public_id,series_id,template_id,title,status) "
                    "VALUES(:id,:series,:template,'private scene guard fixture','PUBLISHED')"
                ),
                {"id": scene, "series": series, "template": template},
            )
            scene_internal = await session.scalar(text("SELECT LAST_INSERT_ID()"))
            await session.execute(
                text(
                    "INSERT INTO scene_revision(public_id,scene_id,version_no,status,"
                    "content_snapshot,"
                    "created_by) VALUES(:id,:scene,1,'PUBLISHED',:content,:actor)"
                ),
                {
                    "id": new_ulid(datetime.now(UTC)),
                    "scene": scene_internal,
                    "actor": identity,
                    "content": json.dumps({"schema_version": 2, "template_type": kind}),
                },
            )
            revision = await session.scalar(text("SELECT LAST_INSERT_ID()"))
            await session.execute(
                text("UPDATE scene SET published_revision_id=:revision WHERE id=:scene"),
                {"revision": revision, "scene": scene_internal},
            )
            await session.execute(
                text(
                    "INSERT INTO content_package(public_id,name,status) "
                    "VALUES(:id,'private scene guard fixture','ACTIVE')"
                ),
                {"id": identity},
            )
            package = await session.scalar(text("SELECT LAST_INSERT_ID()"))
            await session.execute(
                text(
                    "INSERT INTO content_package_item(package_id,content_type,content_id,position) "
                    "VALUES(:package,:kind,:scene,1)"
                ),
                {"package": package, "kind": kind, "scene": scene},
            )
            await session.execute(
                text(
                    "INSERT INTO formal_entitlement(public_id,user_id,package_id,term,status,"
                    "granted_at,updated_at) "
                    "VALUES(:id,:user,:package,'permanent','ACTIVE',:now,:now)"
                ),
                {
                    "id": new_ulid(datetime.now(UTC)),
                    "user": user,
                    "package": package,
                    "now": now.replace(tzinfo=None),
                },
            )
            grant = await session.scalar(text("SELECT LAST_INSERT_ID()"))
        row = {
            "scene": scene_internal,
            "revision": revision,
            "package": package,
            "grant": grant,
            "series": series,
            "template": template,
        }
        self.owned.append((sessions, row, enabled))
        return row

    async def clear(self):
        # 功能:按外键顺序移除本fixture创建的资料并恢复原模块开关。
        while self.owned:
            sessions, row, enabled = self.owned.pop()
            async with sessions() as session, session.begin():
                await session.execute(text("DELETE FROM formal_entitlement WHERE id=:grant"), row)
                await session.execute(
                    text("DELETE FROM content_package_item WHERE package_id=:package"), row
                )
                await session.execute(text("DELETE FROM content_package WHERE id=:package"), row)
                await session.execute(
                    text("UPDATE scene SET published_revision_id=NULL WHERE id=:scene"), row
                )
                await session.execute(text("DELETE FROM scene WHERE id=:scene"), row)
                await session.execute(text("DELETE FROM content_series WHERE id=:series"), row)
                await session.execute(text("DELETE FROM content_template WHERE id=:template"), row)
                await session.execute(
                    text(
                        "UPDATE learning_module_config SET enabled=:enabled "
                        "WHERE module_type='scene_learning'"
                    ),
                    {"enabled": enabled},
                )


@pytest_asyncio.fixture
async def learning_scene():
    # 功能:为SQL测试提供有效场景资料工厂,异常退出也清理本测试记录。
    scenes = SceneFixtures()
    try:
        yield scenes
    finally:
        await scenes.clear()
