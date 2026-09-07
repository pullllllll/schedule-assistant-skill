from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from rank_plan import rank_plan  # noqa: E402


class RankPlanTests(unittest.TestCase):
    def test_rank_plan_rewrites_attention_order_without_moving_entries(self) -> None:
        plan = {
            "plan_meta": {"timezone": "Asia/Shanghai"},
            "task_structure": {
                "goals": [
                    {"id": "p0", "status": "active", "priority": "P0", "deadline": None},
                    {"id": "p1", "status": "active", "priority": "P1", "deadline": None},
                ],
                "nodes": [],
                "todos": [self.todo("early", "p1"), self.todo("late", "p0")],
            },
            "schedule": {
                "generated_at": "2026-09-02T11:00:00+08:00",
                "entries": [self.entry("early-entry", "early", "09:00", 1), self.entry("late-entry", "late", "15:00", 2)],
            },
        }

        ranked = rank_plan(plan, "2026-09-02T12:00:00+08:00")

        entries = {entry["id"]: entry for entry in ranked["schedule"]["entries"]}
        self.assertEqual(entries["late-entry"]["daily_rank"], 1)
        self.assertEqual(entries["early-entry"]["daily_rank"], 2)
        self.assertEqual(entries["early-entry"]["starts_at"], "2026-09-03T09:00:00+08:00")
        self.assertIsNone(entries["early-entry"]["ranking_exception"])
        self.assertEqual(ranked["schedule"]["generated_at"], "2026-09-02T12:00:00+08:00")
        self.assertEqual(plan["schedule"]["entries"][0]["daily_rank"], 1, "input must not be mutated")

    @staticmethod
    def todo(todo_id: str, goal_id: str) -> dict:
        return {
            "id": todo_id,
            "status": "pending",
            "deadline": None,
            "execution_window": {"starts_at": None, "ends_at": None},
            "parent_refs": [{"goal_id": goal_id, "node_id": None}],
            "depends_on_todo_ids": [],
        }

    @staticmethod
    def entry(entry_id: str, todo_id: str, starts: str, rank: int) -> dict:
        return {
            "id": entry_id,
            "source_type": "todo",
            "todo_id": todo_id,
            "starts_at": f"2026-09-03T{starts}:00+08:00",
            "ends_at": f"2026-09-03T{starts[:2]}:30:00+08:00",
            "daily_rank": rank,
            "priority_reason": "旧理由",
            "availability_exception": None,
        }


if __name__ == "__main__":
    unittest.main()
