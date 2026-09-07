#!/usr/bin/env python3
"""Reuse or start the local read-only plan viewer without falling back to fixture data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5180


def emit(payload: dict[str, Any], exit_code: int = 0) -> int:
    stream = sys.stdout if exit_code == 0 else sys.stderr
    print(json.dumps(payload, ensure_ascii=False), file=stream)
    return exit_code


def read_official_plan(path: Path) -> dict[str, Any]:
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError("plan_missing") from error
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("plan_invalid") from error

    meta = plan.get("plan_meta") if isinstance(plan, dict) else None
    version = meta.get("plan_version") if isinstance(meta, dict) else None
    if not isinstance(version, int) or isinstance(version, bool) or version < 0:
        raise ValueError("plan_invalid")
    return plan


def normalize_route(route: str) -> str:
    parsed = urlsplit(route)
    if (
        not route.startswith("/")
        or route.startswith("//")
        or parsed.scheme
        or parsed.netloc
        or parsed.fragment
    ):
        raise ValueError("route_invalid")
    return route


def viewer_url(port: int, route: str) -> str:
    return f"http://{DEFAULT_HOST}:{port}{route}"


def inspect_server(port: int, official_plan: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    endpoint = f"http://{DEFAULT_HOST}:{port}/plan.json"
    request = Request(endpoint, headers={"Cache-Control": "no-cache"})
    try:
        # Local viewer checks must never be routed through a configured HTTP proxy.
        opener = build_opener(ProxyHandler({}))
        with opener.open(request, timeout=1.0) as response:
            source = response.headers.get("X-Plan-Source", "official")
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        return "conflict", {"http_status": error.code}
    except (URLError, TimeoutError, ConnectionError):
        return "stopped", {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return "conflict", {"detail": type(error).__name__}

    if source == "fixture":
        return "conflict", {"source": "fixture"}
    if payload != official_plan:
        return "conflict", {"source": "different_plan"}
    return "running", {}


def stop_started_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=2)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        pass


def log_tail(path: Path, limit: int = 2000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-limit:]


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check, reuse, or start the schedule assistant viewer."
    )
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--route", default="/")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--check-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    workspace = arguments.workspace.expanduser().resolve()

    if not 1 <= arguments.port <= 65535 or arguments.timeout <= 0:
        return emit(
            {"ok": False, "status": "arguments_invalid", "workspace": str(workspace)},
            2,
        )

    try:
        route = normalize_route(arguments.route)
    except ValueError:
        return emit(
            {"ok": False, "status": "route_invalid", "route": arguments.route}, 2
        )

    official_path = workspace / "data" / "plan.json"
    try:
        official_plan = read_official_plan(official_path)
    except ValueError as error:
        status = str(error)
        return emit(
            {
                "ok": False,
                "status": status,
                "official_plan": str(official_path),
            },
            2,
        )

    app_path = workspace / "app"
    if not (app_path / "package.json").is_file():
        return emit(
            {"ok": False, "status": "viewer_missing", "app": str(app_path)}, 3
        )

    url = viewer_url(arguments.port, route)
    server_state, details = inspect_server(arguments.port, official_plan)
    if server_state == "running":
        return emit(
            {
                "ok": True,
                "status": "running",
                "url": url,
                "official_plan": str(official_path),
                "plan_version": official_plan["plan_meta"]["plan_version"],
            }
        )
    if server_state == "conflict":
        return emit(
            {
                "ok": False,
                "status": "port_conflict",
                "port": arguments.port,
                **details,
            },
            3,
        )
    if arguments.check_only:
        return emit(
            {"ok": False, "status": "stopped", "url": url},
            1,
        )

    npm = shutil.which("npm")
    if npm is None:
        return emit({"ok": False, "status": "npm_missing"}, 3)
    if not (app_path / "node_modules").is_dir():
        return emit(
            {
                "ok": False,
                "status": "dependencies_missing",
                "app": str(app_path),
            },
            3,
        )

    workspace_key = hashlib.sha256(str(workspace).encode("utf-8")).hexdigest()[:12]
    log_path = Path(tempfile.gettempdir()) / f"schedule-viewer-{workspace_key}.log"
    command = [
        npm,
        "run",
        "dev",
        "--",
        "--host",
        DEFAULT_HOST,
        "--port",
        str(arguments.port),
        "--strictPort",
    ]
    try:
        with log_path.open("ab") as log_stream:
            process = subprocess.Popen(
                command,
                cwd=app_path,
                stdin=subprocess.DEVNULL,
                stdout=log_stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    except OSError as error:
        return emit(
            {
                "ok": False,
                "status": "start_failed",
                "detail": str(error),
                "log": str(log_path),
            },
            4,
        )

    deadline = time.monotonic() + arguments.timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return emit(
                {
                    "ok": False,
                    "status": "start_failed",
                    "exit_code": process.returncode,
                    "log": str(log_path),
                    "log_tail": log_tail(log_path),
                },
                4,
            )
        server_state, details = inspect_server(arguments.port, official_plan)
        if server_state == "running":
            return emit(
                {
                    "ok": True,
                    "status": "started",
                    "url": url,
                    "pid": process.pid,
                    "log": str(log_path),
                    "official_plan": str(official_path),
                    "plan_version": official_plan["plan_meta"]["plan_version"],
                }
            )
        if server_state == "conflict":
            stop_started_process(process)
            return emit(
                {
                    "ok": False,
                    "status": "port_conflict",
                    "port": arguments.port,
                    **details,
                },
                3,
            )
        time.sleep(0.2)

    stop_started_process(process)
    return emit(
        {
            "ok": False,
            "status": "start_failed",
            "detail": "timeout",
            "log": str(log_path),
            "log_tail": log_tail(log_path),
        },
        4,
    )


if __name__ == "__main__":
    raise SystemExit(main())
