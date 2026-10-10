from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import unittest

import test_apply_plan as fixtures


class RepairScopeTests(unittest.TestCase):
    """Exercise scope decisions through the real transactional CLI."""

    def setUp(self):
        self.original_applier = fixtures.APPLIER
        if os.environ.get("REPAIR_SCOPE_APPLIER"):
            fixtures.APPLIER = Path(os.environ["REPAIR_SCOPE_APPLIER"])
        self.addCleanup(setattr, fixtures, "APPLIER", self.original_applier)
        self.helper = fixtures.ApplyPlanTests()

    def plan(self):
        plan = fixtures.empty_plan()
        a, b, c = [fixtures.todo() for _ in range(3)]
        b.update(id="todo-b", depends_on_todo_ids=["todo-a"])
        c.update(id="todo-c")
        plan["task_structure"]["todos"] = [a, b, c]
        return plan

    def edit(self, plan, index, **changes):
        item = plan["task_structure"]["todos"][index]
        item.update(changes, updated_at="2026-08-18T07:45:00+08:00")

    def state(self):
        state = self.helper.ready_discovery_state()
        state.update(mode="repair_discovery", structural_audit_complete=True)
        return state

    def apply(self, old, new, mode=None, rejected=None, state=None):
        if mode:
            state = self.state() if mode == "repair" else self.helper.incremental_discovery_state()
        result, applied, original_bytes, candidate_after = self.helper.run_apply(
            new, old, discovery_state=state, classifications={"2026-08-18": "workday"})
        self.assertEqual(candidate_after, new)
        payload = json.loads(result.stdout)
        if rejected is not None:
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertEqual(applied, old)
            # run_apply also asserts byte preservation at the filesystem boundary.
            self.assertEqual(original_bytes, json.dumps(old).encode())
            if rejected:
                self.assertEqual(payload["error"], rejected, result.stdout)
        else:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            expected = copy.deepcopy(new)
            expected["plan_meta"].update(plan_version=1, updated_at=fixtures.COMMITTED_AT)
            self.assertEqual(applied, expected)
        return applied

    def test_unrelated_and_cosmetic_changes_do_not_require_repair(self):
        for index, fields in [(2, {"title": "更正独立 C"}), (2, {"remaining_estimate_minutes": 30}),
                              (0, {"title": "同一行动的清楚名称", "context": "保留原意"})]:
            with self.subTest(index=index, fields=fields):
                old = self.plan(); new = copy.deepcopy(old)
                self.edit(new, index, **fields)
                self.apply(old, new)

    def test_related_execution_fields_still_require_repair(self):
        for fields in [{"remaining_estimate_minutes": 30}, {"execution_mode": "single_block"},
                       {"intensity": "low"}, {"status": "completed", "remaining_estimate_minutes": 0}]:
            with self.subTest(fields=fields):
                old = self.plan(); new = copy.deepcopy(old); self.edit(new, 0, **fields)
                self.apply(old, new, rejected="repair_discovery_required")
                self.apply(old, new, mode="repair")

    def scheduled(self):
        plan = self.plan()
        plan["availability_rules"]["calendar_id"] = "CN"
        plan["availability_rules"]["workday"]["segments"] = [
            {"starts_time": "09:00", "ends_time": "18:00", "energy_level": "medium"}]
        plan["schedule"] = {"planning_window": {"starts_on": "2026-08-17", "ends_on": "2026-08-23"},
                            "generated_at": "2026-08-18T07:30:00+08:00", "entries": []}
        for i, letter in enumerate("ab"):
            plan["schedule"]["entries"].append({"id": "entry-" + letter, "source_type": "todo",
                "todo_id": "todo-" + letter, "starts_at": f"2026-08-18T{10+i:02d}:00:00+08:00",
                "ends_at": f"2026-08-18T{11+i:02d}:00:00+08:00", "daily_rank": i+1,
                "priority_reason": None, "ranking_exception": None, "availability_exception": None})
        return plan

    def test_related_schedule_move_is_not_a_structure_audit(self):
        old = self.scheduled(); new = copy.deepcopy(old)
        new["schedule"]["generated_at"] = "2026-08-18T07:45:00+08:00"
        new["schedule"]["entries"][1].update(starts_at="2026-08-18T12:00:00+08:00", ends_at="2026-08-18T13:00:00+08:00")
        self.apply(old, new)
        new["schedule"]["entries"][1].update(starts_at="2026-08-18T10:30:00+08:00", ends_at="2026-08-18T11:30:00+08:00")
        self.apply(old, new, rejected="validation_failed")

    def test_removed_edge_and_new_ownership_still_audit_old_chain(self):
        for ownership in (False, True):
            old = self.plan()
            if ownership:
                old["task_structure"]["goals"] = [fixtures.linear_goal()]
            new = copy.deepcopy(old)
            self.edit(new, 1, **({"parent_refs": [fixtures.parent_ref()]} if ownership else {"depends_on_todo_ids": []}))
            self.apply(old, new, rejected="repair_discovery_required")
            self.apply(old, new, mode="repair")

    def test_appending_to_old_chain_requires_repair(self):
        old = self.plan(); new = copy.deepcopy(old)
        self.edit(new, 2, depends_on_todo_ids=["todo-b"])
        self.apply(old, new, rejected="repair_discovery_required")
        self.apply(old, new, mode="repair")

    def predecessor_plan(self):
        old = self.plan()
        old["task_structure"]["goals"] = [fixtures.linear_goal()]
        old["task_structure"]["todos"][2]["parent_refs"] = [fixtures.parent_ref()]
        old["task_structure"]["todos"][0]["depends_on_todo_ids"] = ["todo-c"]
        return old

    def test_owned_predecessor_changes_reach_unowned_chain(self):
        old = self.predecessor_plan(); new = copy.deepcopy(old)
        self.edit(new, 2, remaining_estimate_minutes=30)
        self.apply(old, new, rejected="repair_discovery_required")
        self.apply(old, new, mode="repair")

    def test_sibling_workload_and_deadline_have_different_directions(self):
        old = self.plan()
        old["task_structure"]["goals"] = [fixtures.linear_goal()]
        old["task_structure"]["todos"][2].update(parent_refs=[fixtures.parent_ref()], depends_on_todo_ids=["todo-a"])
        new = copy.deepcopy(old); self.edit(new, 2, remaining_estimate_minutes=30)
        self.apply(old, new)
        new = copy.deepcopy(old); self.edit(new, 2, deadline="2026-08-23T18:00:00+08:00")
        self.apply(old, new, rejected="repair_discovery_required")
        self.apply(old, new, mode="repair")

    def test_unrelated_new_structure_uses_incremental_not_repair(self):
        old = self.plan(); new = copy.deepcopy(old)
        new["task_structure"]["goals"] = [fixtures.linear_goal()]
        self.apply(old, new, rejected="incremental_discovery_required")
        self.apply(old, new, mode="incremental")

    def test_relation_order_does_not_trigger_discovery(self):
        old = self.predecessor_plan()
        d = fixtures.todo(); d["id"] = "todo-d"
        old["task_structure"]["todos"].append(d)
        old["task_structure"]["todos"][1]["depends_on_todo_ids"] = ["todo-a", "todo-d"]
        new = copy.deepcopy(old)
        self.edit(new, 1, depends_on_todo_ids=["todo-d", "todo-a"])
        self.apply(old, new)

    def test_goal_condition_reaches_chain_but_priority_does_not(self):
        for field, value, related, repair in [("deadline", "2026-08-23T18:00:00+08:00", True, True),
                                             ("status", "paused", True, True),
                                             ("priority", "P0", True, False),
                                             ("deadline", "2026-08-23T18:00:00+08:00", False, False)]:
            with self.subTest(field=field, related=related):
                old = self.predecessor_plan()
                if not related: old["task_structure"]["todos"][0]["depends_on_todo_ids"] = []
                new = copy.deepcopy(old)
                new["task_structure"]["goals"][0].update({field:value, "updated_at":"2026-08-18T07:45:00+08:00"})
                if repair: self.apply(old, new, rejected="repair_discovery_required")
                self.apply(old, new, mode="repair" if repair else None)

    def test_node_conditions_and_edges_follow_actual_membership(self):
        for field, value in [("status", "in_progress"), ("deadline", "2026-08-23T18:00:00+08:00"),
                             ("depends_on_node_ids", ["node-other"])]:
            with self.subTest(field=field):
                old = self.predecessor_plan()
                node = {"id":"node-a", "goal_id":"goal-a", "title":"准备阶段", "outcome":"完成准备",
                        "status":"pending", "starts_on":None, "deadline":None, "depends_on_node_ids":[],
                        "context":None, "created_at":"2026-08-18T07:00:00+08:00", "updated_at":"2026-08-18T07:00:00+08:00"}
                other = copy.deepcopy(node); other["id"] = "node-other"
                old["task_structure"]["nodes"] = [node, other]
                old["task_structure"]["todos"][2]["parent_refs"][0]["node_id"] = "node-a"
                new = copy.deepcopy(old)
                new["task_structure"]["nodes"][0].update({field:value, "updated_at":"2026-08-18T07:45:00+08:00"})
                self.apply(old, new, rejected="repair_discovery_required")
                self.apply(old, new, mode="repair")

    def test_deadline_walk_does_not_fan_out_from_shared_ancestor(self):
        old = self.plan()
        old["task_structure"]["goals"] = [fixtures.linear_goal()]
        x = fixtures.todo(); x.update(id="todo-x", parent_refs=[fixtures.parent_ref()])
        old["task_structure"]["todos"].append(x)
        old["task_structure"]["todos"][0]["depends_on_todo_ids"] = ["todo-x"]
        old["task_structure"]["todos"][2].update(depends_on_todo_ids=["todo-x"], parent_refs=[fixtures.parent_ref()])
        new = copy.deepcopy(old)
        self.edit(new, 2, deadline="2026-08-23T18:00:00+08:00")
        self.apply(old, new)

    def test_invalid_state_is_rejected_even_for_unrelated_change(self):
        old = self.plan(); new = copy.deepcopy(old); self.edit(new, 2, title="独立更名")
        state = self.state(); state["branches"][0]["status"] = "open"
        self.apply(old, new, state=state, rejected="discovery_readiness_failed")

    def test_related_change_cannot_hide_behind_unrelated_change(self):
        old = self.plan(); new = copy.deepcopy(old)
        self.edit(new, 2, title="独立更名"); self.edit(new, 0, remaining_estimate_minutes=30)
        for mutate in [lambda s:s.update(mode="incremental_discovery"),
                       lambda s:s["branches"][0].update(confirmation="pending"),
                       lambda s:s["branches"][0].update(status="open")]:
            state = self.state(); mutate(state)
            self.apply(old, new, state=state, rejected="discovery_readiness_failed")

    def test_cycles_references_and_removal_still_rejected(self):
        for change in ("cycle", "reference", "remove"):
            with self.subTest(change=change):
                old = self.plan(); new = copy.deepcopy(old)
                if change == "remove": new["task_structure"]["todos"].pop(0)
                else: self.edit(new, 0, depends_on_todo_ids=["todo-b" if change == "cycle" else "missing"])
                self.apply(old, new, mode="repair", rejected="")
