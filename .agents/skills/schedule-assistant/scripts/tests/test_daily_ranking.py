from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from daily_ranking import derive_daily_rankings  # noqa: E402


class DailyRankingTests(unittest.TestCase):
    def test_delivery_chain_ranks_before_commute_without_sorting_by_clock(self) -> None:
        plan = {
            "plan_meta": {"timezone": "Asia/Shanghai"},
            "task_structure": {
                "goals": [
                    {"id": "delivery", "status": "active", "priority": "P0", "deadline": None},
                    {"id": "commute", "status": "active", "priority": "P1", "deadline": None},
                ],
                "nodes": [],
                "todos": [
                    self.todo("ride", "commute"),
                    self.todo("meeting", "delivery"),
                    self.todo("revise", "delivery", depends_on=["meeting"]),
                ],
            },
            "schedule": {
                "entries": [
                    self.entry("ride-am", "ride", "08:30", "09:20"),
                    self.entry("meeting-entry", "meeting", "15:00", "17:00"),
                    self.entry("revise-entry", "revise", "17:00", "18:00"),
                    self.entry("ride-pm", "ride", "20:20", "21:00"),
                ]
            },
        }

        result = derive_daily_rankings(plan, "2026-09-02T12:00:00+08:00")

        self.assertEqual(
            [item["entry_id"] for item in result["days"]["2026-09-03"]],
            ["meeting-entry", "revise-entry", "ride-am", "ride-pm"],
        )
        self.assertIn("P0", result["assignments"]["meeting-entry"]["priority_reason"])

    @staticmethod
    def todo(todo_id: str, goal_id: str, depends_on: list[str] | None = None) -> dict:
        return {
            "id": todo_id,
            "status": "pending",
            "deadline": None,
            "execution_window": {"starts_at": None, "ends_at": None},
            "parent_refs": [{"goal_id": goal_id, "node_id": None}],
            "depends_on_todo_ids": depends_on or [],
        }

    @staticmethod
    def entry(entry_id: str, todo_id: str, starts: str, ends: str) -> dict:
        return {
            "id": entry_id,
            "source_type": "todo",
            "todo_id": todo_id,
            "starts_at": f"2026-09-03T{starts}:00+08:00",
            "ends_at": f"2026-09-03T{ends}:00+08:00",
            "daily_rank": 1,
            "priority_reason": None,
            "ranking_exception": None,
            "availability_exception": None,
        }


if __name__ == "__main__":
    unittest.main()
