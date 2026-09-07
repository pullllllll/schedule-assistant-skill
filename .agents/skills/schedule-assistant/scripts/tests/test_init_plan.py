from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "init_plan.py"


class InitPlanTests(unittest.TestCase):
    def run_script(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_creates_contract_empty_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            result = self.run_script(
                str(path),
                "--timezone",
                "Asia/Shanghai",
                "--now",
                "2026-08-20T02:00:00+00:00",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(result.stdout),
                {"ok": True, "path": str(path), "plan_version": 0},
            )

            plan = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                list(plan),
                [
                    "plan_meta",
                    "task_structure",
                    "time_constraints",
                    "availability_rules",
                    "schedule",
                ],
            )
            self.assertEqual(plan["plan_meta"]["plan_version"], 0)
            self.assertEqual(plan["plan_meta"]["timezone"], "Asia/Shanghai")
            self.assertEqual(
                plan["plan_meta"]["updated_at"], "2026-08-20T10:00:00+08:00"
            )
            self.assertIsNone(plan["plan_meta"]["progress_reviewed_through"])
            self.assertEqual(
                plan["task_structure"], {"goals": [], "nodes": [], "todos": []}
            )
            self.assertEqual(plan["time_constraints"], [])
            self.assertIsNone(plan["availability_rules"]["calendar_id"])
            self.assertEqual(plan["availability_rules"]["date_overrides"], [])
            for profile_name in ("workday", "holiday"):
                profile = plan["availability_rules"][profile_name]
                self.assertEqual(profile["segments"], [])
                self.assertIsNone(profile["context"])
                self.assertEqual(profile["created_at"], plan["plan_meta"]["updated_at"])
                self.assertEqual(profile["updated_at"], plan["plan_meta"]["updated_at"])
            self.assertEqual(
                plan["schedule"],
                {"planning_window": None, "generated_at": None, "entries": []},
            )

    def test_does_not_overwrite_existing_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            original = '{"existing": true}\n'
            path.write_text(original, encoding="utf-8")

            result = self.run_script(
                str(path),
                "--timezone",
                "Asia/Shanghai",
                "--now",
                "2026-08-20T10:00:00+08:00",
            )

            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stderr)["error"], "plan_already_exists")
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_rejects_invalid_timezone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            result = self.run_script(str(path), "--timezone", "Mars/Olympus")

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(path.exists())
            self.assertIn("unknown IANA timezone", result.stderr)

    def test_requires_offset_in_explicit_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            result = self.run_script(
                str(path),
                "--timezone",
                "Asia/Shanghai",
                "--now",
                "2026-08-20T10:00:00",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(path.exists())
            self.assertIn("must include a UTC offset", result.stderr)


if __name__ == "__main__":
    unittest.main()
