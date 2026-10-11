from types import SimpleNamespace

import httpx
import pytest

from juya_miniapp_api.integrations.admin_api.client import AdminApiUnavailable
from juya_miniapp_api.integrations.admin_api.schemas import LearningCatalog
from juya_miniapp_api.integrations.admin_api.v15_client import AdminV15Client
from juya_miniapp_api.modules.learning.catalog_service import CatalogService
from juya_miniapp_api.shared.errors import AppError


class Catalog:
    async def get_catalog(self, user, summary):
        return LearningCatalog(items=[{"public_id": "safe"}])


class Features:
    def __init__(self, enabled):
        self.enabled = enabled

    async def learning_features(self):
        if self.enabled is None:
            raise AppError("OPTIONAL_CONFIG_UNAVAILABLE", "Unavailable", 404)
        return SimpleNamespace(profile_completion_enabled=self.enabled)


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False, None])
async def test_catalog_projects_only_authoritative_profile_config_and_preserves_catalog(enabled):
    features = Features(enabled)
    catalog = CatalogService(Catalog(), features=features)
    result = await catalog.catalog("user", {})
    assert result.model_dump()["profile_completion_enabled"] is (enabled is True)
    assert result.items == [{"public_id": "safe"}] and not result.authorization_pending
    features.enabled = False
    assert (await catalog.catalog("user", {})).profile_completion_enabled is False


@pytest.mark.asyncio
async def test_default_and_pending_catalog_keep_profile_entry_closed():
    assert (await CatalogService(Catalog()).catalog("user", {})).profile_completion_enabled is False

    class Pending:
        async def get_catalog(self, *args):
            raise AdminApiUnavailable()

    result = await CatalogService(Pending(), features=Features(True)).catalog("user", {})
    assert result.authorization_pending and result.profile_completion_enabled is False


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False, "true"])
async def test_actual_features_response_preserves_independent_strict_profile_switch(enabled):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "recording_enabled": True,
                    "profile_completion_enabled": enabled,
                    "version": 3,
                },
            )
        ),
        base_url="http://admin.test",
    ) as http:
        client = AdminV15Client(http, secret=b"isolated-profile-config")
        if isinstance(enabled, bool):
            result = await client.learning_features()
            assert result.recording_enabled is True
            assert result.profile_completion_enabled is enabled
        else:
            with pytest.raises(AdminApiUnavailable):
                await client.learning_features()
