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


def test_baseline_schema_snapshot_matches_original_revision():
    from app import models  # noqa: F401
    from app.db import Base

    revision = _load_baseline_module()
    expected = {
        table.name: tuple(column.name for column in table.columns)
        for table in Base.metadata.sorted_tables
    }
    expected["telegram_report_schedules"] = tuple(
        column
        for column in expected["telegram_report_schedules"]
        if column != "force_partial_telegram_sync"
    )
    added_columns = {
        "users": {"job_retention_days", "upload_retention_days"},
        "jobs": {"deletion_requested_at", "cleanup_error"},
        "uploads": {"deletion_requested_at", "cleanup_error"},
    }
    for table, columns in added_columns.items():
        expected[table] = tuple(column for column in expected[table] if column not in columns)
    assert revision.revision == "20260813_0001"
    assert revision.EXPECTED_COLUMNS == expected
    assert all("ALTER TABLE" not in statement for statement in revision.BASELINE_DDL)


def test_force_partial_revision_follows_baseline():
    path = (
        Path(__file__).parents[1]
        / "migrations"
        / "versions"
        / "20260921_0002_force_partial_reports.py"
    )
    spec = importlib.util.spec_from_file_location("force_partial_revision", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.down_revision == "20260813_0001"
    assert module.revision == "20260921_0002"


def test_schema_revision_matches_alembic_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from app.db import SCHEMA_REVISION

    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "migrations"))
    assert ScriptDirectory.from_config(config).get_current_head() == SCHEMA_REVISION


def test_runtime_database_initialization_contains_no_schema_mutations():
    db_source = (Path(__file__).parents[1] / "app" / "db.py").read_text(encoding="utf-8")
    assert "create_all" not in db_source
    assert "ALTER TABLE" not in db_source


def test_production_migration_role_requires_only_database_credentials():
    from app.config import Settings

    settings = Settings(
        _env_file=None,
        app_env="production",
        app_role="migration",
        postgres_host="postgres",
        postgres_db="chat_analyse",
        postgres_user="migration_user",
        postgres_password="migration-password-000000",
    )
    assert settings.app_role == "migration"


def test_retention_revision_follows_indexes_and_compiles_with_named_constraints():
    from io import StringIO

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.db import Base
    from app.models import Job, Upload, User

    path = Path(__file__).parents[1] / "migrations/versions/20260923_0004_job_retention.py"
    spec = importlib.util.spec_from_file_location("retention_revision", path)
    assert spec and spec.loader
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)
    assert revision.down_revision == "20260921_0003"
    assert revision.revision == "20260923_0004"

    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output, "target_metadata": Base.metadata},
    )
    with Operations.context(context):
        revision.upgrade()
    sql = output.getvalue()
    for column in ("job_retention_days", "upload_retention_days"):
        assert f"ADD COLUMN {column} INTEGER" in sql
        constraint = f"ck_users_{column}_range"
        assert constraint in {check.name for check in User.__table__.constraints}
        assert f"CONSTRAINT {constraint} CHECK" in sql
        assert f"{column} IS NULL OR {column} BETWEEN 1 AND 36500" in sql
    for model in (Job, Upload):
        table = model.__tablename__
        assert model.__table__.c.deletion_requested_at.nullable
        assert model.__table__.c.cleanup_error.nullable
        assert f"CREATE INDEX ix_{table}_deletion_requested_at" in sql

    output.seek(0)
    output.truncate()
    with Operations.context(context):
        revision.downgrade()
    assert "DROP COLUMN job_retention_days" in output.getvalue()
    assert "DROP CONSTRAINT ck_users_upload_retention_days_range" in output.getvalue()


@pytest.mark.parametrize("field", ["job_retention_days", "upload_retention_days"])
@pytest.mark.parametrize("days", [0, -1, 36501])
def test_retention_policy_rejects_out_of_range_days(field, days):
    from pydantic import ValidationError
    from app.schemas import RetentionPolicyRequest

    with pytest.raises(ValidationError):
        RetentionPolicyRequest(**{field: days})


def test_retention_policy_can_disable_automatic_deletion():
    from app.schemas import RetentionPolicyRequest

    assert RetentionPolicyRequest().model_dump() == {
        "job_retention_days": None,
        "upload_retention_days": None,
    }
    assert RetentionPolicyRequest(job_retention_days=1, upload_retention_days=36500)


@pytest.mark.parametrize("field", ["cleanup_interval_seconds", "upload_expiry_hours"])
def test_cleanup_configuration_requires_positive_durations(field):
    from pydantic import ValidationError
    from app.config import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="test", app_role="all", **{field: 0})
