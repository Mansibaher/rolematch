"""Test fixtures.

Tests run against a real PostgreSQL database (with pgvector) named by ROLEMATCH_TEST_DATABASE_URL.
The schema is created with Alembic once per session; tables are truncated between tests.
Embeddings use the deterministic, non-semantic hashing backend unless a test opts into MiniLM.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("ROLEMATCH_ENVIRONMENT", "test")
os.environ.setdefault(
    "ROLEMATCH_DATABASE_URL",
    os.environ.get("ROLEMATCH_TEST_DATABASE_URL", "postgresql+psycopg://rolematch:rolematch@localhost:5432/rolematch_test"),
)
os.environ.setdefault("ROLEMATCH_EMBEDDING_BACKEND", "hashing")
os.environ.setdefault("ROLEMATCH_CELERY_EAGER", "true")

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.session import get_engine, get_sessionmaker  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
CSRF = {"X-RoleMatch-CSRF": "1"}


@pytest.fixture(scope="session", autouse=True)
def _schema(tmp_path_factory):
    settings = get_settings()
    settings.upload_dir = tmp_path_factory.mktemp("uploads")
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def _clean():
    yield
    with get_engine().begin() as conn:
        tables = conn.execute(text("select tablename from pg_tables where schemaname='public' and tablename <> 'alembic_version'")).scalars().all()
        conn.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE"))


@pytest.fixture
def db():
    s = get_sessionmaker()()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def client():
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


def register(client: TestClient, email: str = "user@example.com", password: str = "correct-horse-battery", tz: str = "America/Los_Angeles") -> dict:
    r = client.post("/api/auth/register", json={"email": email, "password": password, "timezone": tz}, headers=CSRF)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def user_client(client):
    register(client)
    return client


def demo_resume_text() -> str:
    return (REPO / "fixtures" / "resumes" / "demo_jordan_lee.txt").read_text()
