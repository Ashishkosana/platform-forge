from __future__ import annotations

import argparse
import json
import sys
from uuid import UUID

import httpx

from workflow_engine.config import load_settings


def _base() -> str:
    settings = load_settings()
    host = "127.0.0.1" if settings.api_host in {"0.0.0.0", "::"} else settings.api_host
    return f"http://{host}:{settings.api_port}"


def _client() -> httpx.Client:
    return httpx.Client(base_url=_base(), timeout=10.0)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="workflow", description="CLI for the durable step runner")
    sub = parser.add_subparsers(dest="cmd", required=True)

    submit = sub.add_parser("submit")
    submit.add_argument("workflow")
    submit.add_argument("--key", required=True)
    submit.add_argument("--input", default="{}")

    getp = sub.add_parser("get")
    getp.add_argument("job_id")

    lst = sub.add_parser("list")
    lst.add_argument("--status")
    lst.add_argument("--stuck", action="store_true")

    cancel = sub.add_parser("cancel")
    cancel.add_argument("job_id")

    replay = sub.add_parser("replay")
    replay.add_argument("job_id")
    replay.add_argument("step")

    sub.add_parser("workflows")
    sub.add_parser("health")

    args = parser.parse_args(argv)
    with _client() as client:
        if args.cmd == "submit":
            payload = {
                "workflow_name": args.workflow,
                "idempotency_key": args.key,
                "input": json.loads(args.input),
            }
            resp = client.post("/jobs", json=payload)
        elif args.cmd == "get":
            resp = client.get(f"/jobs/{args.job_id}")
        elif args.cmd == "list":
            params: dict[str, str | bool] = {}
            if args.status:
                params["status"] = args.status
            if args.stuck:
                params["stuck"] = True
            resp = client.get("/jobs", params=params)
        elif args.cmd == "cancel":
            resp = client.post(f"/jobs/{UUID(args.job_id)}/cancel")
        elif args.cmd == "replay":
            resp = client.post(f"/jobs/{UUID(args.job_id)}/steps/{args.step}/replay")
        elif args.cmd == "workflows":
            resp = client.get("/workflows")
        elif args.cmd == "health":
            resp = client.get("/health")
        else:
            parser.error("unknown command")
            return
        sys.stdout.write(json.dumps(resp.json(), indent=2, default=str) + "\n")
        if resp.status_code >= 400:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
