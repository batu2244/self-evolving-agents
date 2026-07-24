import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import database as db  # noqa: E402


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """Bind the module-level engine to a throwaway SQLite file for one test."""
    url = f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setattr(db.config, "DATABASE_URL", url)
    db.reset_engine()
    db.init_db(url)
    yield db
    db.reset_engine()
