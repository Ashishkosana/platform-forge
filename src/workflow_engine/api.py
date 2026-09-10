from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from workflow_engine.claims import (
    cancel_job,
    gauge_snapshot,
    jsonable,
    list_jobs,
    load_job,
    refresh_gauges,
    replay_step,
    submit_job,
)
from workflow_engine.config import Settings, load_settings
from workflow_engine.db import close_pool, configure_pool, connection
from workflow_engine.handlers import bind_settings, register_lab_workflows
from workflow_engine.logging import setup_logging
from workflow_engine.metrics import render_metrics
from workflow_engine.registry import REGISTRY

logger = logging.getLogger("workflow.api")
settings: Settings = load_settings()
_workers: list[subprocess.Popen[bytes]] = []
_gauge_stop = threading.Event()


class SubmitBody(BaseModel):
    workflow_name: str = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=200)
    input: dict[str, Any] = Field(default_factory=dict)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": code, "message": message})


def _gauge_loop() -> None:
    while not _gauge_stop.wait(2.0):
        try:
            with connection() as conn:
                refresh_gauges(conn)
        except Exception:
            logger.exception("gauge refresh failed")


@asynccontextmanager
async def lifespan(_app: FastAPI):  # type: ignore[no-untyped-def]
    global settings
    settings = load_settings()
    setup_logging(settings.log_level)
    bind_settings(settings)
    register_lab_workflows()
    configure_pool(settings.database_url)
    _gauge_stop.clear()
    thread = threading.Thread(target=_gauge_loop, name="gauges", daemon=True)
    thread.start()
    yield
    _gauge_stop.set()
    close_pool()


app = FastAPI(title="durable-workflow-engine", lifespan=lifespan)


@app.middleware("http")
async def limit_body(request: Request, call_next):  # type: ignore[no-untyped-def]
    length = request.headers.get("content-length")
    if length:
        if int(length) > settings.max_body_bytes:
            return _error(413, "payload_too_large", "request body exceeds MAX_BODY_BYTES")
        return await call_next(request)
    body = await request.body()
    if len(body) > settings.max_body_bytes:
        return _error(413, "payload_too_large", "request body exceeds MAX_BODY_BYTES")

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(request.scope, receive)
    return await call_next(request)


@app.get("/health")
def health() -> dict[str, str]:
    with connection() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/metrics")
def metrics() -> Response:
    try:
        with connection() as conn:
            refresh_gauges(conn)
    except Exception:
        logger.exception("metrics gauge refresh failed")
    return Response(content=render_metrics(), media_type="text/plain; version=0.0.4")


@app.get("/workflows")
def workflows() -> dict[str, Any]:
    names = REGISTRY.names()
    out = []
    for name in names:
        wf = REGISTRY.get(name)
        assert wf is not None
        out.append(
            {
                "name": wf.name,
                "steps": [
                    {
                        "name": s.name,
                        "max_attempts": s.max_attempts,
                        "timeout_seconds": s.timeout_seconds,
                    }
                    for s in wf.steps
                ],
            }
        )
    return {"workflows": out}


@app.post("/jobs")
def create_job(body: SubmitBody) -> JSONResponse:
    if REGISTRY.get(body.workflow_name) is None:
        return _error(400, "unknown_workflow", f"unknown workflow {body.workflow_name}")
    with connection() as conn:
        job, created = submit_job(conn, body.workflow_name, body.idempotency_key, body.input)
    return JSONResponse(
        status_code=201 if created else 200,
        content=jsonable(job),
    )


@app.get("/jobs")
def jobs(
    status: str | None = None,
    stuck: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    with connection() as conn:
        items = list_jobs(conn, status=status, stuck=stuck, limit=limit)
    return {"jobs": jsonable(items)}


@app.get("/jobs/{job_id}")
def get_job(job_id: UUID) -> Any:
    try:
        with connection() as conn:
            return jsonable(load_job(conn, job_id))
    except KeyError as err:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": str(job_id)}
        ) from err


@app.post("/jobs/{job_id}/cancel")
def cancel(job_id: UUID) -> Any:
    try:
        with connection() as conn:
            return jsonable(cancel_job(conn, job_id))
    except KeyError as err:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": str(job_id)}
        ) from err
    except PermissionError:
        return _error(409, "conflict_terminal", "job is already terminal")


@app.post("/jobs/{job_id}/steps/{step}/replay")
def replay(job_id: UUID, step: str) -> Any:
    try:
        with connection() as conn:
            return jsonable(replay_step(conn, job_id, step))
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": str(exc)}
        ) from exc
    except PermissionError:
        return _error(409, "not_dead_lettered", "step is not dead-lettered")


@app.get("/lab/effects")
def lab_effects() -> dict[str, Any]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM lab_effects ORDER BY updated_at DESC LIMIT 100"
        ).fetchall()
    return {"effects": jsonable([dict(r) for r in rows])}


@app.get("/ops/snapshot")
def ops_snapshot() -> dict[str, Any]:
    with connection() as conn:
        snap = gauge_snapshot(conn)
    return jsonable(snap)


def _require_demo() -> None:
    if not settings.demo_mode:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "demo mode off"}
        )


def _owned_worker(pid: int) -> subprocess.Popen[bytes] | None:
    for proc in _workers:
        if proc.pid == pid:
            return proc
    return None


def _signal_owned(pid: int, sig: int) -> str | None:
    if _owned_worker(pid) is None:
        return "not_owned"
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        return "not_found"
    return None


@app.get("/demo/workers")
def demo_workers() -> dict[str, Any]:
    _require_demo()
    alive = []
    dead: list[int] = []
    for proc in list(_workers):
        code = proc.poll()
        info = {"pid": proc.pid, "returncode": code, "alive": code is None}
        if code is None:
            alive.append(info)
        else:
            dead.append(proc.pid)
    _workers[:] = [p for p in _workers if p.poll() is None]
    return {"workers": alive, "exited_pids": dead}


@app.post("/demo/workers/start")
def demo_start_worker() -> dict[str, Any]:
    _require_demo()
    env = os.environ.copy()
    env["DATABASE_URL"] = settings.database_url
    env.setdefault("LEASE_TTL_SECONDS", str(settings.lease_ttl_seconds))
    env.setdefault("HEARTBEAT_INTERVAL_SECONDS", str(settings.heartbeat_interval_seconds))
    env.setdefault("POLL_INTERVAL_SECONDS", str(settings.poll_interval_seconds))
    env.setdefault("WORKER_CONCURRENCY", "1")
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "workflow_engine.worker"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _workers.append(proc)
    return {"pid": proc.pid, "alive": True}


@app.post("/demo/workers/{pid}/kill")
def demo_kill_worker(pid: int) -> Any:
    _require_demo()
    reason = _signal_owned(pid, signal.SIGKILL)
    if reason:
        return _error(404, reason, f"pid {pid} is not a demo worker")
    return {"pid": pid, "killed": True, "signal": "SIGKILL"}


@app.post("/demo/workers/{pid}/stop")
def demo_stop_worker(pid: int) -> Any:
    _require_demo()
    reason = _signal_owned(pid, signal.SIGSTOP)
    if reason:
        return _error(404, reason, f"pid {pid} is not a demo worker")
    return {"pid": pid, "stopped": True, "signal": "SIGSTOP"}


@app.post("/demo/workers/{pid}/cont")
def demo_cont_worker(pid: int) -> Any:
    _require_demo()
    reason = _signal_owned(pid, signal.SIGCONT)
    if reason:
        return _error(404, reason, f"pid {pid} is not a demo worker")
    return {"pid": pid, "continued": True, "signal": "SIGCONT"}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    path = Path(__file__).with_name("static") / "index.html"
    return path.read_text(encoding="utf-8")


def main() -> None:
    cfg = load_settings()
    uvicorn.run(
        "workflow_engine.api:app",
        host=cfg.api_host,
        port=cfg.api_port,
        reload=False,
        access_log=False,
    )


if __name__ == "__main__":
    main()
