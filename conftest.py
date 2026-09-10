from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from psycopg import connect
from psycopg.rows import dict_row

from workflow_engine.db import close_pool
from workflow_engine.handlers import register_lab_workflows
from workflow_engine.registry import REGISTRY

TEST_DSN = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://workflow:workflow@127.0.0.1:5432/workflow_test",
)


def _ensure_db() -> None:
    admin = os.environ.get(
        "ADMIN_DATABASE_URL",
        "postgresql://workflow:workflow@127.0.0.1:5432/postgres",
    )
    name = TEST_DSN.rsplit("/", 1)[-1]
    with connect(admin, autocommit=True) as conn:
        row = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        if row is None:
            conn.execute(f'CREATE DATABASE "{name}"')


@pytest.fixture(scope="session", autouse=True)
def _register() -> None:
    if not REGISTRY.names():
        register_lab_workflows()


@pytest.fixture(scope="session")
def database_url() -> str:
    _ensure_db()
    os.environ["DATABASE_URL"] = TEST_DSN
    from workflow_engine.schema import apply_schema

    with connect(TEST_DSN, autocommit=True) as conn:
        apply_schema(conn)
    os.environ["DEMO_MODE"] = "true"
    os.environ["LEASE_TTL_SECONDS"] = "2"
    os.environ["HEARTBEAT_INTERVAL_SECONDS"] = "0.4"
    os.environ["POLL_INTERVAL_SECONDS"] = "0.05"
    os.environ["WORKER_CONCURRENCY"] = "1"
    os.environ["BASE_BACKOFF_SECONDS"] = "0.05"
    os.environ["MAX_BACKOFF_SECONDS"] = "0.4"
    os.environ["API_PORT"] = "43180"
    return TEST_DSN


@pytest.fixture
def client(database_url: str) -> TestClient:
    from workflow_engine.api import app

    with TestClient(app) as test_client:
        yield test_client
    close_pool()


@pytest.fixture(autouse=True)
def clean_db(database_url: str) -> None:
    with connect(database_url, row_factory=dict_row, autocommit=True) as conn:
        conn.execute("TRUNCATE lab_effects, attempts, steps, jobs CASCADE")
    yield
