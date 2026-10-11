from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from juya_miniapp_api.integrations.admin_api.client import AdminApiUnavailable
from juya_miniapp_api.integrations.admin_api.schemas import LearningCatalog
from juya_miniapp_api.modules.learning.catalog_service import CatalogService
from juya_miniapp_api.modules.learning.stage import LearningStageFacts


@pytest.mark.parametrize(
    "entitlement,started,expected",
    [
        (False, False, "NEW"),
        (True, False, "ENTITLED"),
        (False, True, "ENTITLED"),
        (True, True, "ENTITLED"),
    ],
)
def test_stage_is_presentational_and_follows_entitlements_or_non_open_history(
    entitlement, started, expected
):
    assert LearningStageFacts(entitlement, started).stage == expected


@pytest.mark.asyncio
async def test_catalog_uses_account_scoped_authority_even_when_visible_rows_are_empty():
    client, stage = AsyncMock(), AsyncMock()
    client.get_catalog.return_value = LearningCatalog(items=[])
    stage.get.return_value = LearningStageFacts(True, False)
    now = datetime(2026, 10, 11, tzinfo=UTC)
    result = await CatalogService(client, clock=lambda: now, stage_reader=stage).catalog(
        "owner", {}
    )
    assert result.user_stage == "ENTITLED"
    stage.get.assert_awaited_once_with("owner", now)
    assert result.items == []


@pytest.mark.asyncio
async def test_authorization_failure_does_not_guess_a_new_stage():
    client, stage = AsyncMock(), AsyncMock()
    client.get_catalog.side_effect = AdminApiUnavailable()
    result = await CatalogService(client, stage_reader=stage).catalog("owner", {})
    assert result.authorization_pending and result.user_stage is None
    stage.get.assert_not_awaited()
