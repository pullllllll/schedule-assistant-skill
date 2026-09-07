from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


SCRIPT = Path(__file__).resolve().parents[1] / "ensure_viewer.py"


class PlanHandler(BaseHTTPRequestHandler):
    server: "PlanServer"

    def do_GET(self) -> None:
        if self.path != "/plan.json":
            self.send_error(404)
            return
        body = json.dumps(self.server.plan).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if self.server.fixture:
            self.send_header("X-Plan-Source", "fixture")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


class PlanServer(ThreadingHTTPServer):
    plan: dict[str, Any]
    fixture: bool


class EnsureViewerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_workspace(self, plan: dict[str, Any] | None = None) -> dict[str, Any]:
        app = self.workspace / "app"
        app.mkdir(parents=True)
        (app / "package.json").write_text("{}\n", encoding="utf-8")
        official = plan or {
            "plan_meta": {"plan_version": 7, "updated_at": "2026-09-01T09:00:00+08:00"},
            "task_structure": {"goals": [], "nodes": [], "todos": []},
        }
        data = self.workspace / "data"
        data.mkdir()
        (data / "plan.json").write_text(
            json.dumps(official, ensure_ascii=False), encoding="utf-8"
        )
        return official

    def run_script(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(self.workspace), *arguments],
            check=False,
            capture_output=True,
            text=True,
        )

    def start_server(
        self, plan: dict[str, Any], *, fixture: bool = False
    ) -> tuple[PlanServer, threading.Thread]:
        server = PlanServer(("127.0.0.1", 0), PlanHandler)
        server.plan = plan
        server.fixture = fixture
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    @staticmethod
    def stop_server(server: PlanServer, thread: threading.Thread) -> None:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    @staticmethod
    def unused_port() -> int:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])

    def test_reuses_running_viewer_with_the_same_official_plan(self) -> None:
        plan = self.write_workspace()
        server, thread = self.start_server(plan)
        self.addCleanup(self.stop_server, server, thread)

        result = self.run_script(
            "--check-only",
            "--port",
            str(server.server_port),
            "--route",
            "/?page=relations",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "running")
        self.assertEqual(
            payload["url"],
            f"http://127.0.0.1:{server.server_port}/?page=relations",
        )
        self.assertEqual(payload["plan_version"], 7)

    def test_rejects_fixture_even_when_its_payload_matches(self) -> None:
        plan = self.write_workspace()
        server, thread = self.start_server(plan, fixture=True)
        self.addCleanup(self.stop_server, server, thread)

        result = self.run_script("--check-only", "--port", str(server.server_port))

        self.assertEqual(result.returncode, 3)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["status"], "port_conflict")
        self.assertEqual(payload["source"], "fixture")

    def test_does_not_start_when_official_plan_is_missing(self) -> None:
        (self.workspace / "app").mkdir()
        (self.workspace / "app" / "package.json").write_text("{}\n", encoding="utf-8")

        result = self.run_script()

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)["status"], "plan_missing")

    def test_check_only_reports_stopped_without_starting(self) -> None:
        self.write_workspace()
        port = self.unused_port()

        result = self.run_script("--check-only", "--port", str(port))

        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["status"], "stopped")
        self.assertEqual(payload["url"], f"http://127.0.0.1:{port}/")

    def test_rejects_external_route(self) -> None:
        result = self.run_script("--route", "https://example.com/")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)["status"], "route_invalid")


if __name__ == "__main__":
    unittest.main()
