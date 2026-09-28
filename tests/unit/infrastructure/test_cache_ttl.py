from datetime import UTC, datetime, timedelta

from juya_miniapp_api.infrastructure.redis.cache import bounded_cache_ttl

NOW = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)


def test_cache_ttl_never_outlives_entitlement() -> None:
    assert bounded_cache_ttl(NOW, 30, NOW + timedelta(seconds=8, milliseconds=900)) == 8
    assert bounded_cache_ttl(NOW, 30, NOW + timedelta(minutes=1)) == 30
    assert bounded_cache_ttl(NOW, 30, NOW) == 0


def test_cache_ttl_without_entitlement_uses_configured_maximum() -> None:
    assert bounded_cache_ttl(NOW, 60, None) == 60
