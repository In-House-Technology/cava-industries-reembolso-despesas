from io import StringIO
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from app.models import Base

CONFIG = Path(__file__).resolve().parents[1] / "alembic.ini"
BASE_COLUMNS = {"id", "created_at", "created_at_epoch", "modified_at", "modified_at_epoch", "deleted_at", "deleted_at_epoch"}


def test_revision_chain_and_sqlite_upgrade(monkeypatch, tmp_path):
    config = Config(str(CONFIG))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == ["0001_expenses"]
    assert [revision.revision for revision in scripts.walk_revisions()] == ["0001_expenses"]
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "test.db"))
    command.upgrade(config, "head")
    command.upgrade(config, "head")  # An applied revision is not run again.
    engine = create_engine("sqlite:///" + str(tmp_path / "test.db"))
    with engine.connect() as connection:
        columns = {column["name"]: column for column in inspect(connection).get_columns("expenses")}
        assert BASE_COLUMNS <= columns.keys()
        assert columns["id"]["primary_key"]
        assert all(not columns[name]["nullable"] for name in BASE_COLUMNS - {"deleted_at", "deleted_at_epoch"})
        assert columns["deleted_at"]["nullable"] and columns["deleted_at_epoch"]["nullable"]
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    engine.dispose()


def test_postgres_offline_upgrade_and_revision_template(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgres://user@localhost/example")
    output = StringIO()
    config = Config(str(CONFIG), output_buffer=output)
    command.upgrade(config, "head", sql=True)
    assert "CREATE TABLE expenses" in output.getvalue()
    assert "TIMESTAMP WITH TIME ZONE" in output.getvalue()
    assert "UUID" in output.getvalue()
    # Exercise Mako without changing the app's revision chain.
    config.set_main_option("script_location", str(tmp_path))
    (tmp_path / "versions").mkdir()
    (tmp_path / "script.py.mako").write_text((CONFIG.parent / "alembic/script.py.mako").read_text())
    scripts = ScriptDirectory.from_config(config)
    revision = scripts.generate_revision("test_revision", "test template")
    assert revision.down_revision is None
    assert revision.module.upgrade() is None
