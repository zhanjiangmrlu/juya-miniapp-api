"""Private SQL + actual public sockets/JWT/HMAC preserve content-scoped favorites."""

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

from juya_miniapp_api.infrastructure.db.session import create_engine, create_session_factory

_SPEC = importlib.util.spec_from_file_location(
    "v15_namespace_testkit", Path(__file__).parents[2] / "scripts/v15_socket_testkit.py"
)
assert _SPEC and _SPEC.loader
_KIT = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _KIT
_SPEC.loader.exec_module(_KIT)


async def seed_scoped_content(kit):
    kit.resources.verify_containers()
    engine = create_engine(
        kit.resources.database_url.get_secret_value().replace(
            "mysql+pymysql://", "mysql+asyncmy://", 1
        )
    )
    try:
        async with create_session_factory(engine)() as sql, sql.begin():
            for kind, revision in zip(("dialogue", "word_card"), kit.revision_ids[:2], strict=True):
                content = {
                    "schema_version": 2,
                    "template_type": kind,
                    "title_en": "Scoped fixture",
                    "title_zh": "隔离内容",
                    "dialogue": [
                        {
                            "id": "cup-sentence",
                            "speaker": "A",
                            "english": "A medium coffee.",
                            "chinese": "一杯中杯咖啡。",
                        }
                    ]
                    if kind == "dialogue"
                    else [],
                    "vocabulary": [
                        {
                            "entry_id": "medium-" + sense,
                            "sense_id": sense,
                            "source_id": "source-" + sense,
                            "english": "medium",
                            "chinese": meaning,
                            "source_sentence": "A medium coffee.",
                            "source_locator": {"sentence_id": "cup-sentence"}
                            if kind == "dialogue"
                            else None,
                            "confirmed": True,
                        }
                        for sense, meaning in (("cup", "中杯"), ("size", "中等的"))
                    ],
                    "chunks": [],
                }
                await sql.execute(
                    text("UPDATE scene_revision SET content_snapshot=:content WHERE public_id=:id"),
                    {"content": json.dumps(content), "id": revision},
                )
    finally:
        await engine.dispose()


def test_actual_sql_dialogue_word_card_same_local_ids_save_cancel_and_review_independently():
    if not os.getenv("JUYA_TEST_DATABASE_URL") or not os.getenv("JUYA_TEST_REDIS_URL"):
        pytest.skip("central UUID-isolated runner required")
    with _KIT.SocketTestKit(_KIT.PrivateResources.from_environment()) as kit:
        asyncio.run(seed_scoped_content(kit))
        identity = kit.issue_session()
        ids = []
        with kit.client(identity) as client:
            for kind, content_id in zip(("dialogue", "word_card"), kit.scene_ids[:2], strict=True):
                opened = client.post(
                    f"/api/v1/scenes/{content_id}/open",
                    headers={"Idempotency-Key": "open-before-" + kind},
                )
                assert opened.status_code == 200
                assert all(not row["is_favorited"] for row in opened.json()["scene"]["words"])
                for sense in ("cup", "size"):
                    saved = client.post(
                        "/api/v1/favorite-snapshots",
                        headers={"Idempotency-Key": "save-" + kind + sense},
                        json={
                            "content_type": kind,
                            "content_id": content_id,
                            "content_version": 1,
                            "entry_id": "medium-" + sense,
                            "sense_id": sense,
                            "source_id": "source-" + sense,
                        },
                    )
                    assert saved.status_code == 201
                    payload = saved.json()
                    assert payload["content_type"] == kind and payload["content_id"] == content_id
                    ids.append(payload["favorite_id"])
            assert len(set(ids)) == 4
            for index, content_id in enumerate(kit.scene_ids[:2]):
                opened = client.post(
                    f"/api/v1/scenes/{content_id}/open",
                    headers={"Idempotency-Key": f"open-saved-{index}"},
                ).json()["scene"]
                assert {row["favorite_id"] for row in opened["words"]} == set(
                    ids[index * 2 : index * 2 + 2]
                )
            review = client.post(
                "/api/v1/reviews",
                headers={"Idempotency-Key": "review-all-four"},
                json={"card_ids": ids, "snapshot_schema": 2},
            )
            assert review.status_code == 201 and review.json()["card_count"] == 4
            cancelled = client.delete(
                f"/api/v1/favorite-snapshots/{ids[2]}",
                headers={"Idempotency-Key": "cancel-only-word-cup"},
            )
            assert cancelled.status_code == 200
            for position in (0, 1, 3):
                assert client.get(f"/api/v1/favorite-snapshots/{ids[position]}").status_code == 200
            assert client.get(f"/api/v1/favorite-snapshots/{ids[2]}").status_code == 404
            unchanged = client.post(
                f"/api/v1/scenes/{kit.scene_ids[0]}/open",
                headers={"Idempotency-Key": "dialogue-after-other-cancel"},
            ).json()["scene"]
            assert {row["favorite_id"] for row in unchanged["words"]} == set(ids[:2])
            stale = client.post(
                f"/api/v1/reviews/{review.json()['id']}/complete",
                headers={"Idempotency-Key": "reject-stale-four"},
            )
            assert stale.status_code == 409 and stale.json()["code"] == "REVIEW_SNAPSHOT_CHANGED"
            for cards in (ids, [ids[0], ids[0]]):
                denied = client.post(
                    "/api/v1/reviews",
                    headers={"Idempotency-Key": "invalid-" + str(len(cards))},
                    json={"card_ids": cards, "snapshot_schema": 2},
                )
                assert denied.status_code == 422 and denied.json()["code"] == "REVIEW_CARDS_INVALID"
            valid_ids = [ids[0], ids[1], ids[3]]
            valid = client.post(
                "/api/v1/reviews",
                headers={"Idempotency-Key": "review-current-three"},
                json={"card_ids": valid_ids, "snapshot_schema": 2},
            )
            assert valid.status_code == 201 and valid.json()["card_ids"] == valid_ids
            completed = client.post(
                f"/api/v1/reviews/{valid.json()['id']}/complete",
                headers={"Idempotency-Key": "complete-current-three"},
            )
            assert completed.status_code == 200
        stranger = kit.issue_session()
        with kit.client(stranger) as client:
            for content_id in kit.scene_ids[:2]:
                opened = client.post(
                    f"/api/v1/scenes/{content_id}/open",
                    headers={"Idempotency-Key": "stranger-" + content_id},
                ).json()["scene"]
                assert all(not row["is_favorited"] for row in opened["words"])
            denied = client.post(
                "/api/v1/reviews",
                headers={"Idempotency-Key": "foreign-review"},
                json={"card_ids": valid_ids, "snapshot_schema": 2},
            )
            assert denied.status_code == 422
