import json
import sqlite3
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from juya_miniapp_api.integrations.admin_api.schemas import AccessProjection, LearningCatalog
from juya_miniapp_api.modules.checkins.router import create_checkins_router
from juya_miniapp_api.modules.learning.access_service import SQLAlchemyOpenHistory
from juya_miniapp_api.modules.learning.repository import SQLAlchemyLearningRepository
from juya_miniapp_api.modules.learning.router import create_learning_router
from juya_miniapp_api.shared.errors import AppError, install_error_handlers

NOW = datetime(2026, 10, 11, tzinfo=UTC)


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class Session:
    def __init__(self, database):
        self.database = database

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def execute(self, statement, params):
        values = {
            key: value.isoformat(sep=" ") if isinstance(value, datetime) else value
            for key, value in params.items()
        }
        rows = []
        for raw in self.database.execute(str(statement), values).fetchall():
            row = dict(raw)
            for key in ("started_at", "completed_at", "last_learned_at"):
                if row.get(key):
                    row[key] = datetime.fromisoformat(row[key])
            rows.append(row)
        return Rows(rows)


@pytest.fixture
def repository():
    database = sqlite3.connect(":memory:", check_same_thread=False)
    database.row_factory = sqlite3.Row
    database.executescript("""
        CREATE TABLE user_account(id INTEGER, public_id TEXT);
        CREATE TABLE learning_progress(user_id INTEGER, scene_id TEXT, source_type TEXT,
          position TEXT, last_client_sequence INTEGER, started_at TEXT,
          completed_at TEXT, last_learned_at TEXT);
        INSERT INTO user_account VALUES(1,'owner'),(2,'other');
    """)
    for i in range(101):
        stamp = (NOW - timedelta(minutes=i)).replace(tzinfo=None).isoformat(sep=" ")
        database.execute(
            "INSERT INTO learning_progress VALUES(?,?,?,?,?,?,?,?)",
            (
                1,
                f"scene-{i:03}",
                "SCENE",
                json.dumps({"entry_id": "start"}),
                1,
                stamp,
                stamp,
                stamp,
            ),
        )
    yield SQLAlchemyLearningRepository(lambda: Session(database)), database
    database.close()


def history_client(repository, owner="owner"):
    async def identity():
        return owner

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(create_learning_router(None, repository, user_dependency=identity))
    return TestClient(app)


def test_history_101_rows_are_all_reachable_without_false_terminal_page(repository):
    repo, _ = repository
    client = history_client(repo)
    first = client.get("/api/v1/history/scenes").json()
    assert len(first["items"]) == 100
    assert first["has_more"] is True
    assert first["next_cursor"]
    second = client.get("/api/v1/history/scenes", params={"cursor": first["next_cursor"]}).json()
    assert [item["scene_id"] for item in second["items"]] == ["scene-100"]
    assert second["has_more"] is False and second["next_cursor"] is None


def test_history_openapi_exposes_page_cursor_and_owned_progress_shape(repository):
    repo, _ = repository
    schema = history_client(repo).get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/history/scenes"]["get"]
    page_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert "$ref" in page_schema
    page = schema["components"]["schemas"][page_schema["$ref"].split("/")[-1]]
    assert "next_cursor" in page["properties"] and "has_more" in page["properties"]
    item_name = page["properties"]["items"]["items"]["$ref"].split("/")[-1]
    item = schema["components"]["schemas"][item_name]
    assert "last_learned_at" in item["properties"] and "position" in item["properties"]
    parameters = {item["name"]: item for item in operation["parameters"]}
    assert parameters["limit"]["schema"]["maximum"] == 100
    assert "cursor" in parameters


def test_history_timestamp_ties_have_stable_unique_pages_and_user_bound_cursor(repository):
    repo, database = repository
    database.execute("UPDATE learning_progress SET last_learned_at='2026-10-11 00:00:00'")
    client = history_client(repo)
    items, cursor = [], None
    while True:
        params = {"limit": 17}
        if cursor:
            params["cursor"] = cursor
        response = client.get("/api/v1/history/scenes", params=params)
        assert response.status_code == 200
        page = response.json()
        items.extend(item["scene_id"] for item in page["items"])
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]
    assert items == [f"scene-{i:03}" for i in reversed(range(101))]
    assert (
        history_client(repo, "other")
        .get("/api/v1/history/scenes", params={"cursor": cursor})
        .status_code
        == 422
    )
    assert client.get("/api/v1/history/scenes", params={"cursor": "bad"}).status_code == 422


def test_home_review_fallback_can_select_oldest_of_101_scenes(repository):
    repo, _ = repository
    checkins, catalog = AsyncMock(), AsyncMock()
    checkins.list_days.return_value = []
    catalog.catalog.return_value = LearningCatalog(items=[], user_stage="NEW")

    async def access(user, identities):
        # Actual admin AccessBatchRequest allows at most 100 identities per request.
        if len(identities) > 100:
            raise AppError("ADMIN_API_ERROR", "权益查询最多100项", 422)
        return [AccessProjection(scene_id=identity, level="OPEN") for identity in identities]

    catalog.access.side_effect = access

    async def owner():
        return "owner"

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(
        create_checkins_router(
            checkins,
            learning=repo,
            catalog=catalog,
            user_dependency=owner,
        )
    )
    response = TestClient(app).get("/api/v1/home")
    assert response.status_code == 200
    task = response.json()["today_task"]
    assert task["target_id"] == "scene-100"


@pytest.mark.asyncio
async def test_open_history_internal_projection_is_not_truncated(repository):
    repo, _ = repository
    assert len(await SQLAlchemyOpenHistory(repo).list_opened("owner")) == 101
