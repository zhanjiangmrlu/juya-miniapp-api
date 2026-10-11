from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from juya_miniapp_api.integrations.admin_api.schemas import AccessProjection, LearningCatalog
from juya_miniapp_api.modules.checkins.router import create_checkins_router
from juya_miniapp_api.modules.learning.domain import LearningProgress, ReadingPosition

NOW = datetime(2026, 10, 11, 6, tzinfo=UTC)


def progress(identity, days_ago=0, *, completed=False):
    learned = NOW - timedelta(days=days_ago)
    return LearningProgress(
        "owner",
        identity,
        "SCENE",
        ReadingPosition("start"),
        0,
        learned,
        learned if completed else None,
        learned,
    )


def home(*, stage="NEW", history=(), levels=None, catalog_ids=(), cards=(), now=NOW):
    checkins, learning, catalog, snapshots = [AsyncMock() for _ in range(4)]
    checkins.list_days.return_value = []
    learning.list_history.return_value = list(history)
    catalog.catalog.return_value = LearningCatalog(
        user_stage=stage, items=[{"scene_id": identity} for identity in catalog_ids]
    )
    catalog.access.return_value = [
        AccessProjection(scene_id=identity, level=level)
        for identity, level in (levels or {}).items()
    ]
    snapshots.page.return_value = SimpleNamespace(
        items=[SimpleNamespace(favorite_id=card) for card in cards], has_more=False
    )

    async def owner():
        return "owner"

    app = FastAPI()
    app.include_router(
        create_checkins_router(
            checkins,
            user_dependency=owner,
            learning=learning,
            catalog=catalog,
            snapshots=snapshots,
            clock=lambda: now,
        )
    )
    response = TestClient(app).get("/api/v1/home")
    assert response.status_code == 200
    return response.json()


def test_entitled_continues_formal_before_more_recent_open_history():
    result = home(
        stage="ENTITLED",
        history=[progress("open"), progress("formal", 1)],
        levels={"open": "OPEN", "formal": "FORMAL"},
        catalog_ids=["formal"],
    )
    assert result["today_task"]["target_id"] == "formal"


def test_entitled_starts_limited_content_before_unfinished_open_history():
    result = home(
        stage="ENTITLED",
        history=[progress("open")],
        levels={"open": "OPEN", "limited": "LIMITED"},
        catalog_ids=["open", "limited"],
    )
    assert result["today_task"]["kind"] == "NEW_SCENE"
    assert result["today_task"]["target_id"] == "limited"


def test_entitled_favorites_precede_open_scene_review_fallback():
    result = home(
        stage="ENTITLED",
        history=[progress("open")],
        levels={"open": "OPEN"},
        catalog_ids=["open"],
        cards=["favorite"],
    )
    assert result["today_task"]["kind"] == "FAVORITE_REVIEW"
    assert result["today_task"]["card_ids"] == ["favorite"]


def test_entitled_can_revisit_open_history_when_other_tasks_are_absent():
    result = home(
        stage="ENTITLED",
        history=[progress("open")],
        levels={"open": "OPEN"},
    )
    assert result["today_task"]["kind"] == "HISTORY_SCENE"
    assert result["today_task"]["target_id"] == "open"


def test_new_user_keeps_open_scene_learning_path():
    result = home(catalog_ids=["open"], levels={"open": "OPEN"})
    assert result["today_task"]["kind"] == "NEW_SCENE"
    assert result["today_task"]["target_id"] == "open"


def test_review_fallback_uses_oldest_recorded_revisit_and_excludes_inaccessible_history():
    result = home(
        history=[
            progress("recent", 1, completed=True),
            progress("old", 5, completed=True),
            progress("denied", 9, completed=True),
        ],
        levels={"recent": "OPEN", "old": "OPEN", "denied": "PREVIEW"},
    )
    assert result["today_task"]["target_id"] == "old"


@pytest.mark.parametrize("utc_hour,greeting", [(1, "早上好"), (6, "下午好"), (13, "晚上好")])
def test_home_greeting_uses_beijing_hour(utc_hour, greeting):
    result = home(now=NOW.replace(hour=utc_hour))
    assert result["greeting"] == greeting
