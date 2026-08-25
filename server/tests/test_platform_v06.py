from datetime import datetime, timezone
from pathlib import Path

from app.api.protocol import FEATURES
from app.platform.store import cron_matches, next_cron


def test_platform_features_are_advertised():
    assert "background_schedules" in FEATURES
    assert "cloud_worker_leases" in FEATURES
    assert "empirical_route_calibration" in FEATURES
    assert "opentelemetry" in FEATURES


def test_cron_parser_and_next_occurrence():
    value = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    assert cron_matches("0 12 * * *", value)
    assert next_cron("5 12 * * *", value).minute == 5


def test_platform_migration_contains_durable_tables():
    migration = Path(__file__).parents[1] / "app" / "runs" / "migrations" / "007_agent_platform.sql"
    text = migration.read_text(encoding="utf-8")
    assert "agent_platform_schedules" in text
    assert "agent_cloud_tasks" in text
    assert "agent_route_observations" in text


def test_platform_router_is_mounted():
    from app.main import app

    # FastAPI 0.116+ may retain included routers lazily, so OpenAPI is the
    # stable public representation of the mounted endpoint set.
    paths = set(app.openapi()["paths"])
    assert "/platform/schedules" in paths
    assert "/platform/cloud/tasks" in paths
    assert "/platform/cloud/claim" in paths
    assert "/platform/calibration" in paths
