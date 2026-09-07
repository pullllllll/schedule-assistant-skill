from __future__ import annotations

import copy
import fcntl
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1]
APPLIER = SCRIPT_DIR / "apply_plan.py"
sys.path.insert(0, str(SCRIPT_DIR))

from init_plan import build_empty_plan  # noqa: E402


INITIALIZED_AT = datetime.fromisoformat("2026-08-18T07:00:00+08:00")
COMMITTED_AT = "2026-08-18T08:00:00+08:00"


def empty_plan() -> dict:
    return build_empty_plan("Asia/Shanghai", INITIALIZED_AT)


def todo() -> dict:
    return {
        "id": "todo-a",
        "title": "完成任务",
        "parent_refs": [],
        "status": "pending",
        "deadline": None,
        "execution_window": {
            "starts_at": "2026-08-18T09:00:00+08:00",
            "ends_at": "2026-08-23T18:00:00+08:00",
        },
        "remaining_estimate_minutes": 60,
        "execution_mode": "splittable",
        "intensity": "medium",
        "depends_on_todo_ids": [],
        "context": None,
        "created_at": "2026-08-18T07:00:00+08:00",
        "updated_at": "2026-08-18T07:00:00+08:00",
    }


def linear_goal() -> dict:
    return {
        "id": "goal-a",
        "title": "完成交付",
        "type": "linear",
        "status": "active",
        "priority": "P1",
        "outcome": "完成已确认的交付结果",
        "deadline": None,
        "recurrence": None,
        "cycle_template": None,
        "habit_rule": None,
        "context": None,
        "created_at": "2026-08-18T07:00:00+08:00",
        "updated_at": "2026-08-18T07:00:00+08:00",
    }


def parent_ref() -> dict:
    return {
        "goal_id": "goal-a",
        "node_id": None,
        "habit_period_starts_on": None,
        "habit_occurrence_index": None,
    }


class ApplyPlanTests(unittest.TestCase):
    def run_apply(
        self,
        candidate: dict,
        official: dict,
        expected_version: int = 0,
        classifications: dict | None = None,
        discovery_state: dict | None = None,
        held_lock: bool = False,
    ) -> tuple[subprocess.CompletedProcess[str], dict, bytes, dict]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate_path = root / "candidate.json"
            official_path = root / "plan.json"
            candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
            official_path.write_text(json.dumps(official), encoding="utf-8")
            original_bytes = official_path.read_bytes()
            command = [
                sys.executable,
                str(APPLIER),
                str(candidate_path),
                str(official_path),
                "--expected-version",
                str(expected_version),
                "--committed-at",
                COMMITTED_AT,
            ]
            if classifications is not None:
                classification_path = root / "classifications.json"
                classification_path.write_text(json.dumps(classifications), encoding="utf-8")
                command.extend(["--date-classifications", str(classification_path)])
            if discovery_state is not None:
                discovery_path = root / "discovery.json"
                discovery_path.write_text(json.dumps(discovery_state), encoding="utf-8")
                command.extend(["--discovery-state", str(discovery_path)])

            lock_file = None
            if held_lock:
                lock_path = official_path.with_name(f".{official_path.name}.lock")
                lock_file = lock_path.open("a+b")
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                result = subprocess.run(command, check=False, capture_output=True, text=True)
            finally:
                if lock_file:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                    lock_file.close()
            applied = json.loads(official_path.read_text(encoding="utf-8"))
            candidate_after = json.loads(candidate_path.read_text(encoding="utf-8"))
            return result, applied, original_bytes, candidate_after

    def changed_empty_candidate(self) -> tuple[dict, dict]:
        official = empty_plan()
        candidate = copy.deepcopy(official)
        candidate["availability_rules"]["calendar_id"] = "CN"
        return candidate, official

    def ready_discovery_state(self) -> dict:
        return {
            "gate": "scheduling",
            "mode": "first_discovery",
            "breadth_scan_complete": True,
            "structural_audit_complete": False,
            "branches": [
                {
                    "id": "work",
                    "title": "工作交付",
                    "status": "ready",
                    "outcome_check": "complete",
                    "domain_scope_check": "complete",
                    "relationship_check": "complete",
                    "object_classification_check": "complete",
                    "unresolved_questions": [],
                    "structure_source": "agent_derived",
                    "confirmation": "confirmed",
                    "deferred_reason": None,
                }
            ],
        }

    def incremental_discovery_state(self) -> dict:
        state = self.ready_discovery_state()
        state["mode"] = "incremental_discovery"
        state["branches"][0]["structure_source"] = "user_explicit"
        state["branches"][0]["confirmation"] = "not_required"
        return state

    def test_new_owned_todo_requires_incremental_discovery(self) -> None:
        official = empty_plan()
        official["task_structure"]["goals"] = [linear_goal()]
        candidate = copy.deepcopy(official)
        owned = todo()
        owned["parent_refs"] = [parent_ref()]
        candidate["task_structure"]["todos"] = [owned]

        result, applied, _, _ = self.run_apply(candidate, official)

        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["error"], "incremental_discovery_required")
        self.assertEqual(applied, official)

    def test_new_owned_todo_accepts_incremental_discovery(self) -> None:
        official = empty_plan()
        official["task_structure"]["goals"] = [linear_goal()]
        candidate = copy.deepcopy(official)
        owned = todo()
        owned["parent_refs"] = [parent_ref()]
        candidate["task_structure"]["todos"] = [owned]

        result, applied, _, _ = self.run_apply(
            candidate,
            official,
            discovery_state=self.incremental_discovery_state(),
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(applied["task_structure"]["todos"][0]["parent_refs"], [parent_ref()])

    def test_new_todo_dependency_requires_incremental_discovery(self) -> None:
        official = empty_plan()
        first = todo()
        official["task_structure"]["todos"] = [first]
        candidate = copy.deepcopy(official)
        second = todo()
        second["id"] = "todo-b"
        second["depends_on_todo_ids"] = ["todo-a"]
        candidate["task_structure"]["todos"].append(second)

        result, applied, _, _ = self.run_apply(candidate, official)

        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["error"], "incremental_discovery_required")
        self.assertEqual(applied, official)

    def test_explicit_independent_todo_does_not_require_incremental_discovery(self) -> None:
        official = empty_plan()
        official["task_structure"]["goals"] = [linear_goal()]
        candidate = copy.deepcopy(official)
        candidate["task_structure"]["todos"] = [todo()]

        result, applied, _, _ = self.run_apply(candidate, official)

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(applied["task_structure"]["todos"][0]["parent_refs"], [])

    def test_pure_schedule_move_does_not_require_incremental_discovery(self) -> None:
        official = empty_plan()
        official["task_structure"]["todos"] = [todo()]
        official["availability_rules"]["calendar_id"] = "CN"
        official["availability_rules"]["workday"]["segments"] = [
            {"starts_time": "09:00", "ends_time": "18:00", "energy_level": "medium"}
        ]
        official["schedule"] = {
            "planning_window": {"starts_on": "2026-08-17", "ends_on": "2026-08-23"},
            "generated_at": "2026-08-18T07:30:00+08:00",
            "entries": [
                {
                    "id": "entry-a",
                    "source_type": "todo",
                    "starts_at": "2026-08-18T10:00:00+08:00",
                    "ends_at": "2026-08-18T11:00:00+08:00",
                    "todo_id": "todo-a",
                    "daily_rank": 1,
                    "priority_reason": None,
                    "ranking_exception": None,
                    "availability_exception": None,
                }
            ],
        }
        candidate = copy.deepcopy(official)
        candidate["schedule"]["generated_at"] = "2026-08-18T07:45:00+08:00"
        candidate["schedule"]["entries"][0]["starts_at"] = "2026-08-18T11:00:00+08:00"
        candidate["schedule"]["entries"][0]["ends_at"] = "2026-08-18T12:00:00+08:00"

        result, applied, _, _ = self.run_apply(
            candidate,
            official,
            classifications={"2026-08-18": "workday"},
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(applied["schedule"]["entries"][0]["starts_at"], "2026-08-18T11:00:00+08:00")

    def test_unowned_dependency_chain_requires_repair_discovery(self) -> None:
        official = empty_plan()
        first = todo()
        second = todo()
        second["id"] = "todo-b"
        second["title"] = "后续修改"
        second["depends_on_todo_ids"] = ["todo-a"]
        official["task_structure"]["todos"] = [first, second]
        candidate = copy.deepcopy(official)
        candidate["task_structure"]["todos"][1]["remaining_estimate_minutes"] = 30
        candidate["task_structure"]["todos"][1]["updated_at"] = "2026-08-18T07:30:00+08:00"

        result, applied, _, _ = self.run_apply(candidate, official)

        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["error"], "repair_discovery_required")
        self.assertEqual(applied, official)

    def test_unowned_dependency_chain_accepts_completed_repair_discovery(self) -> None:
        official = empty_plan()
        first = todo()
        second = todo()
        second["id"] = "todo-b"
        second["title"] = "后续修改"
        second["depends_on_todo_ids"] = ["todo-a"]
        official["task_structure"]["todos"] = [first, second]
        candidate = copy.deepcopy(official)
        candidate["task_structure"]["todos"][1]["remaining_estimate_minutes"] = 30
        candidate["task_structure"]["todos"][1]["updated_at"] = "2026-08-18T07:30:00+08:00"
        state = self.ready_discovery_state()
        state["mode"] = "repair_discovery"
        state["structural_audit_complete"] = True

        result, applied, _, _ = self.run_apply(candidate, official, discovery_state=state)

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(applied["task_structure"]["todos"][1]["remaining_estimate_minutes"], 30)

    def test_first_structure_commit_requires_discovery_readiness(self) -> None:
        official = empty_plan()
        candidate = copy.deepcopy(official)
        candidate["task_structure"]["todos"] = [todo()]

        result, applied, _, _ = self.run_apply(candidate, official)

        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["error"], "discovery_readiness_required")
        self.assertEqual(applied, official)

    def test_first_structure_commit_accepts_valid_discovery_readiness(self) -> None:
        official = empty_plan()
        candidate = copy.deepcopy(official)
        candidate["task_structure"]["todos"] = [todo()]

        result, applied, _, _ = self.run_apply(
            candidate,
            official,
            discovery_state=self.ready_discovery_state(),
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(applied["task_structure"]["todos"][0]["id"], "todo-a")

    def test_invalid_discovery_readiness_cannot_be_bypassed(self) -> None:
        official = empty_plan()
        candidate = copy.deepcopy(official)
        candidate["task_structure"]["todos"] = [todo()]
        state = self.ready_discovery_state()
        state["branches"][0]["status"] = "open"

        result, applied, _, _ = self.run_apply(candidate, official, discovery_state=state)

        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["error"], "discovery_readiness_failed")
        self.assertEqual(applied, official)

    def test_success_increments_version_and_does_not_modify_candidate(self) -> None:
        candidate, official = self.changed_empty_candidate()
        result, applied, _, candidate_after = self.run_apply(candidate, official)
        output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, output)
        self.assertTrue(output["ok"])
        self.assertEqual(output["previous_version"], 0)
        self.assertEqual(output["plan_version"], 1)
        self.assertEqual(applied["plan_meta"]["plan_version"], 1)
        self.assertEqual(applied["plan_meta"]["updated_at"], COMMITTED_AT)
        self.assertEqual(applied["availability_rules"]["calendar_id"], "CN")
        self.assertEqual(candidate_after, candidate)

    def test_version_conflict_leaves_official_bytes_unchanged(self) -> None:
        candidate, official = self.changed_empty_candidate()
        official["plan_meta"]["plan_version"] = 1
        result, applied, original_bytes, _ = self.run_apply(candidate, official, expected_version=0)
        output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 2)
        self.assertEqual(output["error"], "version_conflict")
        self.assertEqual(json.dumps(applied).encode(), json.dumps(official).encode())

    def test_candidate_version_mismatch_is_rejected(self) -> None:
        candidate, official = self.changed_empty_candidate()
        candidate["plan_meta"]["plan_version"] = 1
        result, applied, _, _ = self.run_apply(candidate, official, expected_version=0)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["error"], "candidate_version_mismatch")
        self.assertEqual(applied, official)

    def test_invalid_candidate_is_not_committed(self) -> None:
        candidate, official = self.changed_empty_candidate()
        del candidate["schedule"]
        result, applied, _, _ = self.run_apply(candidate, official)
        output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertEqual(output["error"], "validation_failed")
        self.assertEqual(applied, official)

    def test_progress_checkpoint_cannot_move_backward(self) -> None:
        official = empty_plan()
        official["plan_meta"]["progress_reviewed_through"] = "2026-08-18T07:30:00+08:00"
        candidate = copy.deepcopy(official)
        candidate["plan_meta"]["progress_reviewed_through"] = None
        candidate["availability_rules"]["calendar_id"] = "CN"
        result, applied, _, _ = self.run_apply(candidate, official)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "progress_checkpoint_rollback",
            {item["code"] for item in json.loads(result.stdout)["errors"]},
        )
        self.assertEqual(applied, official)

    def test_existing_todo_cannot_be_physically_removed(self) -> None:
        official = empty_plan()
        official["task_structure"]["todos"] = [todo()]
        candidate = copy.deepcopy(official)
        candidate["task_structure"]["todos"] = []
        result, applied, _, _ = self.run_apply(candidate, official)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "persistent_object_removed",
            {item["code"] for item in json.loads(result.stdout)["errors"]},
        )
        self.assertEqual(applied, official)

    def test_changed_object_requires_updated_at_change(self) -> None:
        official = empty_plan()
        official["task_structure"]["todos"] = [todo()]
        candidate = copy.deepcopy(official)
        candidate["task_structure"]["todos"][0]["title"] = "修改后的任务"
        result, applied, _, _ = self.run_apply(candidate, official)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "updated_at_not_advanced",
            {item["code"] for item in json.loads(result.stdout)["errors"]},
        )
        self.assertEqual(applied, official)

    def test_warning_does_not_block_commit(self) -> None:
        official = empty_plan()
        official["task_structure"]["todos"] = [todo()]
        official["availability_rules"]["calendar_id"] = "CN"
        official["availability_rules"]["workday"]["segments"] = [
            {"starts_time": "09:00", "ends_time": "18:00", "energy_level": "medium"}
        ]
        official["schedule"] = {
            "planning_window": {"starts_on": "2026-08-17", "ends_on": "2026-08-23"},
            "generated_at": "2026-08-18T07:30:00+08:00",
            "entries": [
                {
                    "id": "entry-a", "source_type": "todo",
                    "starts_at": "2026-08-18T10:00:00+08:00", "ends_at": "2026-08-18T11:00:00+08:00",
                    "todo_id": "todo-a", "daily_rank": 1, "priority_reason": None,
                    "ranking_exception": None,
                    "availability_exception": None,
                }
            ],
        }
        candidate = copy.deepcopy(official)
        candidate["task_structure"]["todos"][0]["remaining_estimate_minutes"] = 30
        candidate["task_structure"]["todos"][0]["updated_at"] = "2026-08-18T07:45:00+08:00"
        result, applied, _, _ = self.run_apply(
            candidate,
            official,
            classifications={"2026-08-18": "workday"},
        )
        output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, output)
        self.assertIn("future_coverage_exceeds_remaining", {item["code"] for item in output["warnings"]})
        self.assertEqual(applied["plan_meta"]["plan_version"], 1)

    def test_no_change_commit_is_rejected(self) -> None:
        official = empty_plan()
        result, applied, _, _ = self.run_apply(copy.deepcopy(official), official)
        self.assertEqual(result.returncode, 1)
        self.assertIn("no_changes", {item["code"] for item in json.loads(result.stdout)["errors"]})
        self.assertEqual(applied, official)

    def test_busy_lock_is_rejected_without_modification(self) -> None:
        candidate, official = self.changed_empty_candidate()
        result, applied, _, _ = self.run_apply(candidate, official, held_lock=True)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stdout)["error"], "plan_busy")
        self.assertEqual(applied, official)

    def test_changed_object_updated_at_must_move_forward(self) -> None:
        official = empty_plan()
        official["task_structure"]["todos"] = [todo()]
        candidate = copy.deepcopy(official)
        candidate["task_structure"]["todos"][0]["title"] = "修改后的任务"
        candidate["task_structure"]["todos"][0]["updated_at"] = "2026-08-18T06:00:00+08:00"
        result, applied, _, _ = self.run_apply(candidate, official)
        self.assertEqual(result.returncode, 1)
        self.assertIn("updated_at_not_advanced", {item["code"] for item in json.loads(result.stdout)["errors"]})
        self.assertEqual(applied, official)

    def test_schedule_change_requires_generated_at_advance(self) -> None:
        official = empty_plan()
        official["schedule"] = {
            "planning_window": {"starts_on": "2026-08-17", "ends_on": "2026-08-23"},
            "generated_at": "2026-08-18T07:00:00+08:00",
            "entries": [],
        }
        candidate = copy.deepcopy(official)
        candidate["schedule"]["planning_window"] = {
            "starts_on": "2026-08-24",
            "ends_on": "2026-08-30",
        }
        result, applied, _, _ = self.run_apply(candidate, official)
        self.assertEqual(result.returncode, 1)
        self.assertIn("generated_at_not_advanced", {item["code"] for item in json.loads(result.stdout)["errors"]})
        self.assertEqual(applied, official)


if __name__ == "__main__":
    unittest.main()
