from __future__ import annotations

import logging
import os
import random
import signal
import socket
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from prometheus_client import start_http_server
from psycopg import Connection

from workflow_engine.claims import (
    ClaimedStep,
    claim_step,
    complete_failure,
    complete_success,
    heartbeat,
)
from workflow_engine.config import Settings, load_settings
from workflow_engine.db import configure_pool, connection, get_pool
from workflow_engine.handlers import bind_settings, register_lab_workflows
from workflow_engine.logging import log_event, setup_logging
from workflow_engine.metrics import HANDLER_SECONDS
from workflow_engine.models import StepContext
from workflow_engine.registry import REGISTRY

logger = logging.getLogger("workflow.worker")


class Heartbeat:
    def __init__(
        self,
        claimed: ClaimedStep,
        worker_id: str,
        settings: Settings,
    ) -> None:
        self.claimed = claimed
        self.worker_id = worker_id
        self.settings = settings
        self.stop = threading.Event()
        self.lost = threading.Event()
        self.cancelled = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="heartbeat", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def join(self) -> None:
        self.stop.set()
        self.thread.join(timeout=2)

    def should_stop(self) -> bool:
        return self.lost.is_set() or self.cancelled.is_set()

    def _loop(self) -> None:
        while not self.stop.wait(self.settings.heartbeat_interval_seconds):
            try:
                with connection() as conn:
                    status = heartbeat(
                        conn,
                        self.claimed.step_id,
                        self.worker_id,
                        self.claimed.fencing_token,
                        self.settings.lease_ttl_sql,
                    )
            except Exception:
                logger.exception("heartbeat failed")
                self.lost.set()
                return
            if status is None:
                self.lost.set()
                log_event(
                    logger,
                    "heartbeat_lost",
                    job_id=str(self.claimed.job_id),
                    step_id=str(self.claimed.step_id),
                    step_name=self.claimed.name,
                    fencing_token=self.claimed.fencing_token,
                    attempt_count=self.claimed.attempt_count,
                    worker_id=self.worker_id,
                )
                return
            if status == "cancelled":
                self.cancelled.set()
                return


def _effect_key(claimed: ClaimedStep) -> str:
    return f"{claimed.workflow_name}:{claimed.idempotency_key}:{claimed.name}"


@contextmanager
def _handler_conn() -> Iterator[Connection]:  # type: ignore[type-arg]
    with get_pool().connection() as conn:
        yield conn


def _run_handler(claimed: ClaimedStep, hb: Heartbeat) -> tuple[dict[str, Any] | None, str | None]:
    handler = REGISTRY.handler(claimed.workflow_name, claimed.name)
    if handler is None:
        return None, f"unregistered handler {claimed.workflow_name}.{claimed.name}"

    ctx = StepContext(
        job_id=claimed.job_id,
        step_id=claimed.step_id,
        step_name=claimed.name,
        seq=claimed.seq,
        attempt_count=claimed.attempt_count,
        fencing_token=claimed.fencing_token,
        worker_id=hb.worker_id,
        idempotency_key=_effect_key(claimed),
        workflow_name=claimed.workflow_name,
        job_input=claimed.job_input,
        upstream_outputs=claimed.upstream_outputs,
        should_stop=hb.should_stop,
        get_conn=_handler_conn,
    )
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["out"] = handler(ctx)
        except Exception as exc:  # noqa: BLE001
            box["err"] = exc

    thread = threading.Thread(target=target, name=f"handler-{claimed.name}", daemon=True)
    started = time.perf_counter()
    thread.start()
    deadline = time.monotonic() + claimed.timeout_seconds
    while thread.is_alive():
        if hb.should_stop():
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        thread.join(min(0.1, remaining))
    HANDLER_SECONDS.labels(step=claimed.name).observe(time.perf_counter() - started)

    if "err" in box:
        return None, f"{type(box['err']).__name__}: {box['err']}"
    if "out" in box:
        out = box["out"]
        if not isinstance(out, dict):
            return None, "handler must return a dict"
        return out, None
    if hb.cancelled.is_set():
        return None, "cancelled"
    if hb.lost.is_set():
        return None, "lease_lost"
    if thread.is_alive():
        return None, "timeout"
    return None, "handler exited without result"


def execute_claimed(claimed: ClaimedStep, worker_id: str, settings: Settings) -> None:
    rng = random.SystemRandom()
    hb = Heartbeat(claimed, worker_id, settings)
    hb.start()
    log_event(
        logger,
        "claimed",
        job_id=str(claimed.job_id),
        step_id=str(claimed.step_id),
        step_name=claimed.name,
        fencing_token=claimed.fencing_token,
        attempt_count=claimed.attempt_count,
        worker_id=worker_id,
        steal=claimed.steal,
    )
    try:
        output, error = _run_handler(claimed, hb)
    finally:
        hb.join()

    with connection() as conn:
        if output is not None:
            ok = complete_success(conn, claimed, output)
            log_event(
                logger,
                "succeeded" if ok else "fence_rejected",
                job_id=str(claimed.job_id),
                step_id=str(claimed.step_id),
                step_name=claimed.name,
                fencing_token=claimed.fencing_token,
                attempt_count=claimed.attempt_count,
                worker_id=worker_id,
            )
            return
        if error == "cancelled" or hb.cancelled.is_set():
            complete_failure(
                conn,
                claimed,
                "cancelled",
                "cancelled",
                settings.base_backoff_seconds,
                settings.max_backoff_seconds,
                rng,
            )
            log_event(
                logger,
                "cancelled",
                job_id=str(claimed.job_id),
                step_id=str(claimed.step_id),
                step_name=claimed.name,
                fencing_token=claimed.fencing_token,
                attempt_count=claimed.attempt_count,
                worker_id=worker_id,
            )
            return
        if error == "lease_lost" or hb.lost.is_set():
            complete_failure(
                conn,
                claimed,
                error or "lease_lost",
                "rejected_fence",
                settings.base_backoff_seconds,
                settings.max_backoff_seconds,
                rng,
            )
            log_event(
                logger,
                "fence_rejected",
                job_id=str(claimed.job_id),
                step_id=str(claimed.step_id),
                step_name=claimed.name,
                fencing_token=claimed.fencing_token,
                attempt_count=claimed.attempt_count,
                worker_id=worker_id,
            )
            return
        outcome = "timeout" if error == "timeout" else "failed"
        result = complete_failure(
            conn,
            claimed,
            error or "failed",
            outcome,
            settings.base_backoff_seconds,
            settings.max_backoff_seconds,
            rng,
        )
        log_event(
            logger,
            "dead_lettered" if result == "dead_lettered" else "failed",
            job_id=str(claimed.job_id),
            step_id=str(claimed.step_id),
            step_name=claimed.name,
            fencing_token=claimed.fencing_token,
            attempt_count=claimed.attempt_count,
            worker_id=worker_id,
            result=result,
            error=error,
        )


def slot_loop(slot: int, settings: Settings, stop: threading.Event, boot: str) -> None:
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{slot}:{boot}"
    while not stop.is_set():
        try:
            with connection() as conn:
                claimed = claim_step(conn, worker_id, settings.lease_ttl_sql)
        except Exception:
            logger.exception("claim failed")
            stop.wait(min(2.0, settings.poll_interval_seconds * 4))
            continue
        if claimed is None:
            stop.wait(settings.poll_interval_seconds)
            continue
        try:
            execute_claimed(claimed, worker_id, settings)
        except Exception:
            logger.exception("execute failed")
            stop.wait(0.5)


def main() -> None:
    settings = load_settings()
    setup_logging(settings.log_level)
    bind_settings(settings)
    register_lab_workflows()
    configure_pool(settings.database_url, max_size=max(4, settings.worker_concurrency * 3))
    if settings.worker_metrics_port:
        start_http_server(settings.worker_metrics_port)
    stop = threading.Event()

    def handle_signal(signum: int, _frame: object) -> None:
        log_event(logger, "shutdown", signal=signum)
        stop.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    boot = uuid.uuid4().hex[:8]
    log_event(
        logger,
        "worker_boot",
        pid=os.getpid(),
        concurrency=settings.worker_concurrency,
        boot=boot,
    )
    threads = [
        threading.Thread(target=slot_loop, args=(i, settings, stop, boot), daemon=True)
        for i in range(max(1, settings.worker_concurrency))
    ]
    for thread in threads:
        thread.start()
    try:
        while not stop.is_set():
            stop.wait(0.5)
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
