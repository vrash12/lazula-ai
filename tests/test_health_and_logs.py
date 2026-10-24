"""Health endpoints, log hygiene (TESTING.md §3) and the migration matching the models."""

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from app.models import Base
from tests.conftest import Hub

SENTINEL = "SENTINEL-7f3a-do-not-log"


async def test_health_and_ready(hub: Hub) -> None:
    assert (await hub.client.get("/health")).json() == {"status": "ok"}
    ready = await hub.client.get("/ready")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}


async def test_content_never_appears_in_logs_or_errors(
    hub: Hub, capsys: pytest.CaptureFixture[str]
) -> None:
    long_context = f"Sermon idea containing {SENTINEL} that must never be logged anywhere at all."
    ok = await hub.run_task("sermon.captions", {"context": long_context})
    hub.fake.script("primary", "unavailable")
    hub.fake.script("secondary", "unavailable")
    failed = await hub.run_task("sermon.captions", {"context": long_context})
    bad = await hub.run_task("sermon.captions", {"context": SENTINEL[:10]})
    assert ok.status_code == 200 and failed.status_code == 503 and bad.status_code == 422
    captured = capsys.readouterr()
    assert "task_completed" in captured.out  # logging is on
    assert SENTINEL not in captured.out + captured.err
    assert SENTINEL not in failed.text + bad.text


def test_migration_matches_models(tmp_path: pytest.TempPathFactory) -> None:
    db_file = tmp_path / "migrate.sqlite"  # type: ignore[operator]
    config = Config("alembic.ini")
    config.attributes["database_url"] = f"sqlite+aiosqlite:///{db_file}"
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{db_file}")
    with engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    engine.dispose()
    assert diff == []
    command.downgrade(config, "base")
