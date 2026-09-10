from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from workflow_engine.schema import apply_schema

_pool: ConnectionPool | None = None


def configure_pool(dsn: str, max_size: int = 16) -> ConnectionPool:
    global _pool
    if _pool is not None:
        _pool.close()
    _pool = ConnectionPool(
        conninfo=dsn,
        min_size=1,
        max_size=max_size,
        kwargs={"row_factory": dict_row, "autocommit": False},
        open=True,
    )
    with _pool.connection() as conn:
        apply_schema(conn)
    return _pool


def get_pool() -> ConnectionPool:
    if _pool is None:
        raise RuntimeError("database pool is not configured")
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def connection() -> Iterator[Connection]:  # type: ignore[type-arg]
    with get_pool().connection() as conn:
        yield conn
