from typing import Protocol

from juya_miniapp_api.integrations.admin_api.v15_content import ConfirmedEntry, SceneDetail
from juya_miniapp_api.modules.favorites.snapshots import SnapshotIdentity


class FavoriteStates(Protocol):
    async def states(
        self, user_id: str, identities: list[SnapshotIdentity]
    ) -> dict[SnapshotIdentity, str]: ...


async def attach_favorite_states(
    user_id: str, detail: SceneDetail, repository: FavoriteStates
) -> SceneDetail:
    entries = [*detail.words, *detail.chunks]
    identities: list[SnapshotIdentity] = [
        (
            detail.content_type,
            detail.content_id,
            row.entry_type,
            row.entry_id,
            row.sense_id,
            row.source_id,
        )
        for row in entries
    ]
    states = await repository.states(user_id, identities)

    def enrich(row: ConfirmedEntry) -> ConfirmedEntry:
        favorite_id = states.get(
            (
                detail.content_type,
                detail.content_id,
                row.entry_type,
                row.entry_id,
                row.sense_id,
                row.source_id,
            )
        )
        return row.model_copy(
            update={"is_favorited": favorite_id is not None, "favorite_id": favorite_id}
        )

    return detail.model_copy(
        update={
            "words": [enrich(row) for row in detail.words],
            "chunks": [enrich(row) for row in detail.chunks],
        }
    )
