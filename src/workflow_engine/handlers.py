from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from workflow_engine.config import Settings
from workflow_engine.models import StepContext

_settings: Settings | None = None


def bind_settings(settings: Settings) -> None:
    global _settings
    _settings = settings


def _allowed_hosts() -> frozenset[str]:
    if _settings is None:
        return frozenset({"127.0.0.1", "localhost"})
    return _settings.allowed_hosts


def noop(_ctx: StepContext) -> dict[str, Any]:
    return {}


def echo(ctx: StepContext) -> dict[str, Any]:
    return {"echo": ctx.job_input}


def fail_until(ctx: StepContext) -> dict[str, Any]:
    until = int(ctx.job_input.get("fail_until", 3))
    if ctx.attempt_count < until:
        raise RuntimeError(f"fail_until: attempt {ctx.attempt_count} < {until}")
    return {"passed_on_attempt": ctx.attempt_count}


def always_fail(_ctx: StepContext) -> dict[str, Any]:
    raise RuntimeError("always_fail")


def hang(ctx: StepContext) -> dict[str, Any]:
    seconds = float(ctx.job_input.get("seconds", 30))
    deadline = time.time() + seconds
    while time.time() < deadline:
        time.sleep(0.05)
    return {"slept": seconds}


def _write_naive(ctx: StepContext) -> None:
    with ctx.get_conn() as conn:
        conn.execute(
            """
            INSERT INTO lab_effects
              (effect_key, write_count, first_token, last_token, last_worker_id)
            VALUES (%(key)s, 1, %(token)s, %(token)s, %(worker)s)
            ON CONFLICT (effect_key) DO UPDATE SET
              write_count = lab_effects.write_count + 1,
              last_token = EXCLUDED.last_token,
              last_worker_id = EXCLUDED.last_worker_id,
              updated_at = now()
            """,
            {
                "key": ctx.idempotency_key,
                "token": ctx.fencing_token,
                "worker": ctx.worker_id,
            },
        )
        conn.commit()


def _write_idempotent(ctx: StepContext) -> bool:
    with ctx.get_conn() as conn:
        row = conn.execute(
            """
            INSERT INTO lab_effects
              (effect_key, write_count, first_token, last_token, last_worker_id)
            VALUES (%(key)s, 1, %(token)s, %(token)s, %(worker)s)
            ON CONFLICT (effect_key) DO NOTHING
            RETURNING effect_key
            """,
            {
                "key": ctx.idempotency_key,
                "token": ctx.fencing_token,
                "worker": ctx.worker_id,
            },
        ).fetchone()
        conn.commit()
        return row is not None


def pg_effect_naive(ctx: StepContext) -> dict[str, Any]:
    _write_naive(ctx)
    return {"mode": "naive", "effect_key": ctx.idempotency_key}


def pg_effect_idempotent(ctx: StepContext) -> dict[str, Any]:
    inserted = _write_idempotent(ctx)
    return {"mode": "idempotent", "effect_key": ctx.idempotency_key, "inserted": inserted}


def crash_after_naive(ctx: StepContext) -> dict[str, Any]:
    _write_naive(ctx)
    if ctx.attempt_count <= 1:
        os._exit(1)
    return {"mode": "naive", "recovered": True, "effect_key": ctx.idempotency_key}


def crash_after_idempotent(ctx: StepContext) -> dict[str, Any]:
    _write_idempotent(ctx)
    if ctx.attempt_count <= 1:
        os._exit(1)
    return {"mode": "idempotent", "recovered": True, "effect_key": ctx.idempotency_key}


def http_post(ctx: StepContext) -> dict[str, Any]:
    url = str(ctx.job_input.get("url") or "")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("http_post only allows http/https URLs")
    host = (parsed.hostname or "").lower()
    if host not in _allowed_hosts():
        raise ValueError(f"http_post host {host!r} is not in ALLOWED_HTTP_HOSTS")
    payload = ctx.job_input.get("body", ctx.job_input)
    timeout = float(ctx.job_input.get("http_timeout_seconds", 5))
    response = httpx.post(
        url,
        json=payload,
        headers={"Idempotency-Key": ctx.idempotency_key, "X-Fencing-Token": str(ctx.fencing_token)},
        timeout=timeout,
    )
    response.raise_for_status()
    body: Any
    try:
        body = response.json()
    except Exception:
        body = response.text[:500]
    return {"status_code": response.status_code, "body": body}


def register_lab_workflows() -> None:
    from workflow_engine.registry import REGISTRY, Step, Workflow

    specs: list[tuple[str, list[tuple[str, Any, int, int]]]] = [
        ("noop", [("run", noop, 1, 5)]),
        ("echo", [("run", echo, 1, 5)]),
        ("naive_effect", [("write", pg_effect_naive, 3, 10)]),
        ("idempotent_effect", [("write", pg_effect_idempotent, 3, 10)]),
        ("crash_naive", [("boom", crash_after_naive, 5, 10)]),
        ("crash_idempotent", [("boom", crash_after_idempotent, 5, 10)]),
        ("fail_until", [("run", fail_until, 10, 10)]),
        ("always_fail", [("run", always_fail, 3, 5)]),
        ("hang", [("run", hang, 1, 120)]),
        ("http_post", [("run", http_post, 5, 10)]),
        (
            "linear_demo",
            [
                ("charge", pg_effect_idempotent, 5, 10),
                ("notify", echo, 5, 10),
                ("provision", noop, 3, 10),
            ],
        ),
        (
            "linear_naive",
            [
                ("charge", pg_effect_naive, 5, 10),
                ("notify", echo, 5, 10),
            ],
        ),
    ]
    for name, steps in specs:
        REGISTRY.register(
            Workflow(
                name=name,
                steps=[
                    Step(sname, handler, max_attempts=max_a, timeout_seconds=timeout)
                    for sname, handler, max_a, timeout in steps
                ],
            )
        )
