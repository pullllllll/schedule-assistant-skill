from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


SCRIPT_DIR = Path(__file__).resolve().parents[1]
VALIDATOR = SCRIPT_DIR / "validate_plan.py"
sys.path.insert(0, str(SCRIPT_DIR))

from init_plan import build_empty_plan  # noqa: E402


NOW = "2026-08-18T08:00:00+08:00"
TIMESTAMP = datetime.fromisoformat("2026-08-18T07:00:00+08:00")


def empty_plan() -> dict:
    return build_empty_plan("Asia/Shanghai", TIMESTAMP)


def base_todo(todo_id: str = "todo-a") -> dict:
    return {
        "id": todo_id,
        "title": "完成任务",
        "parent_refs": [],
        "status": "pending",
        "deadline": None,
        "execution_window": {
            "starts_at": "2026-08-17T09:00:00+08:00",
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


def todo_entry(entry_id: str = "entry-a", todo_id: str = "todo-a", rank: int = 1) -> dict:
    return {
        "id": entry_id,
        "source_type": "todo",
        "starts_at": "2026-08-18T10:00:00+08:00",
        "ends_at": "2026-08-18T11:00:00+08:00",
        "todo_id": todo_id,
        "daily_rank": rank,
        "priority_reason": None,
        "ranking_exception": None,
        "availability_exception": None,
    }


def linear_goal(goal_id: str, priority: str) -> dict:
    return {
        "id": goal_id,
        "title": goal_id,
        "type": "linear",
        "status": "active",
        "priority": priority,
        "outcome": "完成结果",
        "deadline": None,
        "recurrence": None,
        "cycle_template": None,
        "habit_rule": None,
        "context": None,
        "created_at": "2026-08-18T07:00:00+08:00",
        "updated_at": "2026-08-18T07:00:00+08:00",
    }


def scheduled_plan() -> dict:
    plan = empty_plan()
    plan["task_structure"]["todos"].append(base_todo())
    plan["availability_rules"]["calendar_id"] = "CN"
    plan["availability_rules"]["workday"]["segments"] = [
        {"starts_time": "09:00", "ends_time": "18:00", "energy_level": "medium"}
    ]
    plan["schedule"] = {
        "planning_window": {"starts_on": "2026-08-17", "ends_on": "2026-08-23"},
        "generated_at": "2026-08-18T07:30:00+08:00",
        "entries": [todo_entry()],
    }
    return plan


class ValidatePlanTests(unittest.TestCase):
    def validate(self, plan: dict, classifications: dict | None = None) -> tuple[int, dict]:
        with tempfile.TemporaryDirectory() as directory:
            plan_path = Path(directory) / "plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            command = [sys.executable, str(VALIDATOR), str(plan_path), "--calculated-at", NOW]
            if classifications is not None:
                classification_path = Path(directory) / "classifications.json"
                classification_path.write_text(json.dumps(classifications), encoding="utf-8")
                command.extend(["--date-classifications", str(classification_path)])
            result = subprocess.run(command, check=False, capture_output=True, text=True)
            return result.returncode, json.loads(result.stdout)

    def codes(self, result: dict, level: str = "errors") -> set[str]:
        return {item["code"] for item in result[level]}

    def test_empty_plan_is_valid(self) -> None:
        code, result = self.validate(empty_plan())
        self.assertEqual(code, 0, result)
        self.assertTrue(result["ok"])

    def test_valid_scheduled_plan_with_classification(self) -> None:
        code, result = self.validate(scheduled_plan(), {"2026-08-18": "workday"})
        self.assertEqual(code, 0, result)
        self.assertTrue(result["ok"])

    def test_missing_date_classification_blocks_scheduled_todo(self) -> None:
        code, result = self.validate(scheduled_plan())
        self.assertEqual(code, 1)
        self.assertIn("missing_date_classification", self.codes(result))

    def test_date_override_supplies_complete_profile_without_classification(self) -> None:
        plan = scheduled_plan()
        plan["availability_rules"]["date_overrides"] = [
            {
                "id": "override-1",
                "starts_on": "2026-08-18",
                "ends_on": "2026-08-18",
                "segments": [{"starts_time": "09:00", "ends_time": "12:00", "energy_level": "medium"}],
                "context": None,
                "created_at": "2026-08-18T07:00:00+08:00",
                "updated_at": "2026-08-18T07:00:00+08:00",
            }
        ]
        code, result = self.validate(plan)
        self.assertEqual(code, 0, result)

    def test_missing_reference_and_dependency_cycle_are_errors(self) -> None:
        plan = empty_plan()
        first = base_todo("todo-a")
        second = base_todo("todo-b")
        first["depends_on_todo_ids"] = ["todo-b"]
        second["depends_on_todo_ids"] = ["todo-a", "missing"]
        plan["task_structure"]["todos"] = [first, second]
        code, result = self.validate(plan)
        self.assertEqual(code, 1)
        self.assertIn("missing_reference", self.codes(result))
        self.assertIn("todo_dependency_cycle", self.codes(result))

    def test_todo_overlap_is_error_but_time_constraint_overlap_is_allowed(self) -> None:
        plan = scheduled_plan()
        second = base_todo("todo-b")
        plan["task_structure"]["todos"].append(second)
        second_entry = todo_entry("entry-b", "todo-b", 2)
        second_entry["starts_at"] = "2026-08-18T10:30:00+08:00"
        second_entry["ends_at"] = "2026-08-18T11:30:00+08:00"
        plan["schedule"]["entries"].append(second_entry)
        code, result = self.validate(plan, {"2026-08-18": "workday"})
        self.assertEqual(code, 1)
        self.assertIn("illegal_schedule_overlap", self.codes(result))

        plan = empty_plan()
        for suffix in ("a", "b"):
            plan["time_constraints"].append(
                {
                    "id": f"tc-{suffix}", "kind": "commitment", "title": suffix,
                    "status": "active", "starts_at": "2026-08-18T10:00:00+08:00",
                    "ends_at": "2026-08-18T11:00:00+08:00", "recurrence": None,
                    "context": None, "created_at": "2026-08-18T07:00:00+08:00",
                    "updated_at": "2026-08-18T07:00:00+08:00",
                }
            )
        plan["schedule"] = {
            "planning_window": {"starts_on": "2026-08-17", "ends_on": "2026-08-23"},
            "generated_at": "2026-08-18T07:30:00+08:00",
            "entries": [
                {"id": "tc-entry-a", "source_type": "time_constraint", "starts_at": "2026-08-18T10:00:00+08:00", "ends_at": "2026-08-18T11:00:00+08:00", "time_constraint_id": "tc-a"},
                {"id": "tc-entry-b", "source_type": "time_constraint", "starts_at": "2026-08-18T10:00:00+08:00", "ends_at": "2026-08-18T11:00:00+08:00", "time_constraint_id": "tc-b"},
            ],
        }
        code, result = self.validate(plan)
        self.assertEqual(code, 0, result)

    def test_missing_time_constraint_projection_is_error(self) -> None:
        plan = empty_plan()
        plan["time_constraints"] = [
            {
                "id": "tc-a", "kind": "commitment", "title": "会议", "status": "active",
                "starts_at": "2026-08-18T14:00:00+08:00", "ends_at": "2026-08-18T15:00:00+08:00",
                "recurrence": None, "context": None, "created_at": "2026-08-18T07:00:00+08:00",
                "updated_at": "2026-08-18T07:00:00+08:00",
            }
        ]
        plan["schedule"] = {
            "planning_window": {"starts_on": "2026-08-17", "ends_on": "2026-08-23"},
            "generated_at": "2026-08-18T07:30:00+08:00",
            "entries": [],
        }
        code, result = self.validate(plan)
        self.assertEqual(code, 1)
        self.assertIn("missing_time_constraint_projection", self.codes(result))

    def test_future_coverage_excess_is_warning(self) -> None:
        plan = scheduled_plan()
        plan["task_structure"]["todos"][0]["remaining_estimate_minutes"] = 30
        code, result = self.validate(plan, {"2026-08-18": "workday"})
        self.assertEqual(code, 0, result)
        self.assertIn("future_coverage_exceeds_remaining", self.codes(result, "warnings"))

    def test_daily_rank_must_be_continuous(self) -> None:
        plan = scheduled_plan()
        plan["schedule"]["entries"][0]["daily_rank"] = 2
        code, result = self.validate(plan, {"2026-08-18": "workday"})
        self.assertEqual(code, 1)
        self.assertIn("invalid_daily_rank", self.codes(result))

    def test_daily_rank_must_match_semantic_attention_order(self) -> None:
        plan = scheduled_plan()
        low_goal = linear_goal("goal-low", "P1")
        high_goal = linear_goal("goal-high", "P0")
        plan["task_structure"]["goals"] = [low_goal, high_goal]
        first = plan["task_structure"]["todos"][0]
        first["parent_refs"] = [{"goal_id": "goal-low", "node_id": None, "habit_period_starts_on": None, "habit_occurrence_index": None}]
        second = base_todo("todo-b")
        second["parent_refs"] = [{"goal_id": "goal-high", "node_id": None, "habit_period_starts_on": None, "habit_occurrence_index": None}]
        plan["task_structure"]["todos"].append(second)
        second_entry = todo_entry("entry-b", "todo-b", 2)
        second_entry["starts_at"] = "2026-08-18T12:00:00+08:00"
        second_entry["ends_at"] = "2026-08-18T13:00:00+08:00"
        plan["schedule"]["entries"].append(second_entry)

        code, result = self.validate(plan, {"2026-08-18": "workday"})

        self.assertEqual(code, 1)
        self.assertIn("semantic_daily_rank_mismatch", self.codes(result))

    def test_confirmed_date_ranking_exception_allows_user_order(self) -> None:
        plan = scheduled_plan()
        low_goal = linear_goal("goal-low", "P1")
        high_goal = linear_goal("goal-high", "P0")
        plan["task_structure"]["goals"] = [low_goal, high_goal]
        first = plan["task_structure"]["todos"][0]
        first["parent_refs"] = [{"goal_id": "goal-low", "node_id": None, "habit_period_starts_on": None, "habit_occurrence_index": None}]
        second = base_todo("todo-b")
        second["parent_refs"] = [{"goal_id": "goal-high", "node_id": None, "habit_period_starts_on": None, "habit_occurrence_index": None}]
        plan["task_structure"]["todos"].append(second)
        second_entry = todo_entry("entry-b", "todo-b", 2)
        second_entry["starts_at"] = "2026-08-18T12:00:00+08:00"
        second_entry["ends_at"] = "2026-08-18T13:00:00+08:00"
        plan["schedule"]["entries"].append(second_entry)
        exception = {"confirmed_at": "2026-08-18T08:00:00+08:00", "reason": "用户指定当天先做低优先级事项"}
        for entry in plan["schedule"]["entries"]:
            entry["ranking_exception"] = exception

        code, result = self.validate(plan, {"2026-08-18": "workday"})

        self.assertEqual(code, 0, result)

    def test_terminal_todo_cannot_keep_future_entry(self) -> None:
        plan = scheduled_plan()
        todo = plan["task_structure"]["todos"][0]
        todo["status"] = "completed"
        todo["remaining_estimate_minutes"] = 0
        code, result = self.validate(plan, {"2026-08-18": "workday"})
        self.assertEqual(code, 1)
        self.assertIn("terminal_todo_future_entry", self.codes(result))

    def test_dependency_requires_full_predecessor_coverage(self) -> None:
        plan = scheduled_plan()
        predecessor = plan["task_structure"]["todos"][0]
        predecessor["remaining_estimate_minutes"] = 120
        dependent = base_todo("todo-b")
        dependent["depends_on_todo_ids"] = ["todo-a"]
        plan["task_structure"]["todos"].append(dependent)
        dependent_entry = todo_entry("entry-b", "todo-b", 2)
        dependent_entry["starts_at"] = "2026-08-18T12:00:00+08:00"
        dependent_entry["ends_at"] = "2026-08-18T13:00:00+08:00"
        plan["schedule"]["entries"].append(dependent_entry)
        code, result = self.validate(plan, {"2026-08-18": "workday"})
        self.assertEqual(code, 1)
        self.assertIn("dependency_completion_unknown", self.codes(result))

    def test_in_progress_full_block_can_supply_dependency_completion_time(self) -> None:
        plan = scheduled_plan()
        predecessor_entry = plan["schedule"]["entries"][0]
        predecessor_entry["starts_at"] = "2026-08-18T07:30:00+08:00"
        predecessor_entry["ends_at"] = "2026-08-18T08:30:00+08:00"
        dependent = base_todo("todo-b")
        dependent["depends_on_todo_ids"] = ["todo-a"]
        plan["task_structure"]["todos"].append(dependent)
        dependent_entry = todo_entry("entry-b", "todo-b", 2)
        dependent_entry["starts_at"] = "2026-08-18T09:00:00+08:00"
        dependent_entry["ends_at"] = "2026-08-18T10:00:00+08:00"
        plan["schedule"]["entries"].append(dependent_entry)

        code, result = self.validate(plan, {"2026-08-18": "workday"})

        self.assertEqual(code, 0, result)

    def test_rejects_non_rfc3339_datetime_even_if_python_can_parse_it(self) -> None:
        plan = empty_plan()
        plan["plan_meta"]["updated_at"] = "2026-08-18 07:00:00+08:00"
        code, result = self.validate(plan)
        self.assertEqual(code, 1)
        self.assertIn("invalid_datetime", self.codes(result))

    def test_monthly_recurrence_clips_from_original_anchor_and_applies_reschedule(self) -> None:
        plan = empty_plan()
        plan["time_constraints"] = [
            {
                "id": "tc-monthly", "kind": "commitment", "title": "月末会议", "status": "active",
                "starts_at": "2026-01-31T10:00:00+08:00", "ends_at": "2026-01-31T11:00:00+08:00",
                "recurrence": {
                    "interval": 1, "unit": "month", "ends_on": None,
                    "exceptions": [
                        {
                            "occurrence_starts_at": "2026-02-28T10:00:00+08:00",
                            "action": "reschedule",
                            "starts_at": "2026-02-27T15:00:00+08:00",
                            "ends_at": "2026-02-27T16:00:00+08:00",
                        }
                    ],
                },
                "context": None, "created_at": "2026-01-01T10:00:00+08:00",
                "updated_at": "2026-01-01T10:00:00+08:00",
            }
        ]
        plan["schedule"] = {
            "planning_window": {"starts_on": "2026-02-23", "ends_on": "2026-03-01"},
            "generated_at": "2026-02-23T08:00:00+08:00",
            "entries": [
                {
                    "id": "tc-entry", "source_type": "time_constraint",
                    "starts_at": "2026-02-27T15:00:00+08:00", "ends_at": "2026-02-27T16:00:00+08:00",
                    "time_constraint_id": "tc-monthly",
                }
            ],
        }
        code, result = self.validate(plan)
        self.assertEqual(code, 0, result)

    def test_cross_week_time_constraint_may_start_before_window(self) -> None:
        plan = empty_plan()
        plan["time_constraints"] = [
            {
                "id": "tc-trip", "kind": "unavailable", "title": "旅行", "status": "active",
                "starts_at": "2026-08-16T20:00:00+08:00", "ends_at": "2026-08-17T08:00:00+08:00",
                "recurrence": None, "context": None, "created_at": "2026-08-16T10:00:00+08:00",
                "updated_at": "2026-08-16T10:00:00+08:00",
            }
        ]
        plan["schedule"] = {
            "planning_window": {"starts_on": "2026-08-17", "ends_on": "2026-08-23"},
            "generated_at": "2026-08-16T12:00:00+08:00",
            "entries": [
                {
                    "id": "tc-trip-entry", "source_type": "time_constraint",
                    "starts_at": "2026-08-16T20:00:00+08:00", "ends_at": "2026-08-17T08:00:00+08:00",
                    "time_constraint_id": "tc-trip",
                }
            ],
        }
        code, result = self.validate(plan)
        self.assertEqual(code, 0, result)

    def test_required_habit_window_is_hard_error(self) -> None:
        plan = scheduled_plan()
        goal = {
            "id": "goal-habit", "title": "跑步", "type": "habit", "status": "active",
            "priority": "P1", "outcome": "保持训练", "deadline": None, "recurrence": None,
            "cycle_template": None,
            "habit_rule": {
                "interval": 1, "unit": "week", "first_period_starts_on": "2026-08-17",
                "minimum_count": 1, "session_minutes": 60, "spacing_rule": None,
                "preferred_windows": [
                    {"weekdays": [2], "starts_time": "07:00", "ends_time": "09:00", "strictness": "required"}
                ],
            },
            "context": None, "created_at": "2026-08-17T08:00:00+08:00",
            "updated_at": "2026-08-17T08:00:00+08:00",
        }
        plan["task_structure"]["goals"].append(goal)
        plan["task_structure"]["todos"][0]["parent_refs"] = [
            {
                "goal_id": "goal-habit", "node_id": None,
                "habit_period_starts_on": "2026-08-17", "habit_occurrence_index": 1,
            }
        ]
        code, result = self.validate(plan, {"2026-08-18": "workday"})
        self.assertEqual(code, 1)
        self.assertIn("habit_required_window", self.codes(result))

    def test_habit_todo_can_also_advance_linear_goal(self) -> None:
        plan = empty_plan()
        plan["task_structure"]["goals"] = [
            {
                "id": "goal-cycling", "title": "保持骑行通勤", "type": "habit",
                "status": "active", "priority": "P1", "outcome": "每周完成骑行通勤",
                "deadline": None, "recurrence": None, "cycle_template": None,
                "habit_rule": {
                    "interval": 1, "unit": "week", "first_period_starts_on": "2026-08-17",
                    "minimum_count": 1, "session_minutes": 60, "spacing_rule": None,
                    "preferred_windows": [],
                },
                "context": None, "created_at": "2026-08-17T08:00:00+08:00",
                "updated_at": "2026-08-17T08:00:00+08:00",
            },
            {
                "id": "goal-weight-loss", "title": "完成阶段减重", "type": "linear",
                "status": "active", "priority": "P1", "outcome": "达到已确认的阶段减重结果",
                "deadline": None, "recurrence": None, "cycle_template": None,
                "habit_rule": None, "context": None,
                "created_at": "2026-08-17T08:00:00+08:00",
                "updated_at": "2026-08-17T08:00:00+08:00",
            },
        ]
        todo = base_todo("todo-cycling-1")
        todo["parent_refs"] = [
            {
                "goal_id": "goal-cycling", "node_id": None,
                "habit_period_starts_on": "2026-08-17", "habit_occurrence_index": 1,
            },
            {
                "goal_id": "goal-weight-loss", "node_id": None,
                "habit_period_starts_on": None, "habit_occurrence_index": None,
            },
        ]
        plan["task_structure"]["todos"] = [todo]
        code, result = self.validate(plan)
        self.assertEqual(code, 0, result)
        self.assertTrue(result["ok"])

    def test_retained_past_todo_does_not_require_calendar_revalidation(self) -> None:
        plan = scheduled_plan()
        entry = plan["schedule"]["entries"][0]
        entry["starts_at"] = "2026-08-17T10:00:00+08:00"
        entry["ends_at"] = "2026-08-17T11:00:00+08:00"
        code, result = self.validate(plan)
        self.assertEqual(code, 0, result)


if __name__ == "__main__":
    unittest.main()
