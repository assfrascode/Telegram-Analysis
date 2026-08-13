import importlib.util
from pathlib import Path

import pytest


pytest.importorskip("alembic")


def _load_baseline_module():
    path = (
        Path(__file__).parents[1]
        / "migrations"
        / "versions"
        / "20260813_0001_baseline.py"
    )
    spec = importlib.util.spec_from_file_location("baseline_revision", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_baseline_schema_snapshot_matches_current_models():
    from app import models  # noqa: F401
    from app.db import Base, SCHEMA_REVISION

    revision = _load_baseline_module()
    expected = {
        table.name: tuple(column.name for column in table.columns)
        for table in Base.metadata.sorted_tables
    }
    assert revision.revision == SCHEMA_REVISION
    assert revision.EXPECTED_COLUMNS == expected
    assert all("ALTER TABLE" not in statement for statement in revision.BASELINE_DDL)


def test_runtime_database_initialization_contains_no_schema_mutations():
    db_source = (Path(__file__).parents[1] / "app" / "db.py").read_text(encoding="utf-8")
    assert "create_all" not in db_source
    assert "ALTER TABLE" not in db_source


def test_production_migration_role_requires_only_database_credentials():
    from app.config import Settings

    settings = Settings(
        app_env="production",
        app_role="migration",
        postgres_host="postgres",
        postgres_db="chat_analyse",
        postgres_user="migration_user",
        postgres_password="migration-password-000000",
    )
    assert settings.app_role == "migration"
