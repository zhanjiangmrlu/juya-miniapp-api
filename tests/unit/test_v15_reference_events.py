import json
from datetime import UTC, datetime

import pytest

from juya_miniapp_api.modules.favorites.snapshots import FavoriteSnapshot
from juya_miniapp_api.modules.favorites.snapshots_sql import SQLAlchemySnapshotRepository


@pytest.mark.asyncio
async def test_legacy_release_event_uses_internal_canonical_type_without_rewriting_saved_facts():
    row = FavoriteSnapshot.model_validate(
        {
            "favorite_id": "saved",
            "active": False,
            "snapshot_version": 1,
            "snapshot_revision_id": "saved-r1",
            "saved_content_version": 1,
            "favorited_at": datetime.now(UTC),
            "data": {
                "reference": {
                    "content_type": "SCENE",
                    "content_id": "source",
                    "content_version": 1,
                    "entry_type": "word",
                    "entry_id": "word",
                    "sense_id": "sense",
                    "source_id": "source-id",
                },
                "revision_id": "published-r1",
                "english": "saved word",
                "phonetic": "",
                "meaning_zh": "旧词",
                "explanation": "",
                "source_sentence": "saved sentence",
                "source_title": "saved title",
                "source_locator": {},
                "legacy_incomplete": True,
                "image_asset_id": "fixed-image",
            },
        }
    )
    saved = row.model_dump()

    class Session:
        def __init__(self):
            self.writes = []

        async def scalar(self, *args):
            return 3

        async def execute(self, statement, params):
            self.writes.append(params)

    session = Session()
    await SQLAlchemySnapshotRepository._reference(session, row, "saved-r1", datetime.now(UTC))
    event = json.loads(session.writes[1]["payload"])
    assert event["content_type"] == "dialogue"
    assert event["sequence"] == 4 and event["active"] is False
    assert event["image_asset_id"] == "fixed-image"
    assert event["revision_id"] == "published-r1"
    assert row.model_dump() == saved

    # Backfill preserves known fields even when no published revision was saved;
    # it creates no held resource projection for that unresolvable reference.
    unresolved = row.model_copy(update={"data": row.data.model_copy(update={"revision_id": None})})
    empty_session = Session()
    await SQLAlchemySnapshotRepository._reference(
        empty_session, unresolved, "saved-r1", datetime.now(UTC)
    )
    assert empty_session.writes == []
    assert unresolved.data.image_asset_id == "fixed-image"
