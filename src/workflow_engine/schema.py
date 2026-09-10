from __future__ import annotations

from pathlib import Path

from psycopg import Connection


def schema_path() -> Path:
    pkg = Path(__file__).with_name("schema.sql")
    root = Path(__file__).resolve().parents[2] / "schema.sql"
    if root.exists():
        return root
    if pkg.exists():
        return pkg
    raise FileNotFoundError("schema.sql not found")


def split_sql(script: str) -> list[str]:
    statements: list[str] = []
    buf: list[str] = []
    for line in script.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") or stripped == "":
            if buf:
                buf.append(line)
            continue
        buf.append(line)
        if stripped.endswith(";"):
            stmt = "\n".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def apply_schema(conn: Connection) -> None:  # type: ignore[type-arg]
    sql = schema_path().read_text(encoding="utf-8")
    for statement in split_sql(sql):
        conn.execute(statement)
    conn.commit()
