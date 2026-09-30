from pathlib import Path

import pytest

from juya_miniapp_api.infrastructure.config import Settings
from juya_miniapp_api.main import create_app

ROOT = Path(__file__).resolve().parents[2]


def test_container_runs_non_root_and_worker_has_no_public_port() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8")

    assert "USER juya" in dockerfile
    assert "read_only: true" in compose
    worker_block = compose.split("  miniapp-worker:", 1)[1].split("\nvolumes:", 1)[0]
    assert "ports:" not in worker_block


@pytest.mark.asyncio
async def test_production_readiness_rejects_missing_configuration() -> None:
    with pytest.raises(RuntimeError, match="OSS"):
        create_app(Settings(environment="production"))
