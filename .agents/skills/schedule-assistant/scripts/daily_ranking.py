#!/usr/bin/env python3
"""Derive deterministic per-day attention order for todo schedule entries."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo


PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}
TERMINAL_STATUSES = {"completed", "cancelled"}


def parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except ValueError:
        return None


def _indexed(items: Iterable[Any]) -> Dict[str, Dict[str, Any]]:
    return {
        item["id"]: item
        for item in items
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }


def _active_parents(
    todo: Dict[str, Any], goals: Dict[str, Dict[str, Any]], nodes: Dict[str, Dict[str, Any]]
) -> List[Tuple[Dict[str, Any], Optional[Dict[str, Any]]]]:
    result: List[Tuple[Dict[str, Any], Optional[Dict[str, Any]]]] = []
    for ref in todo.get("parent_refs", []):
        if not isinstance(ref, dict):
            continue
        goal = goals.get(ref.get("goal_id"))
        node = nodes.get(ref.get("node_id")) if ref.get("node_id") else None
        if not goal or goal.get("status") != "active":
            continue
        if node and node.get("status") in TERMINAL_STATUSES:
            continue
        result.append((goal, node))
    return result


def _effective_deadline(
    todo: Dict[str, Any], parents: List[Tuple[Dict[str, Any], Optional[Dict[str, Any]]]]
) -> Optional[datetime]:
    values = [parse_datetime(todo.get("deadline"))]
    for goal, node in parents:
        values.append(parse_datetime(goal.get("deadline")))
        if node:
            values.append(parse_datetime(node.get("deadline")))
    present = [value for value in values if value is not None]
    return min(present) if present else None


def _urgency(deadline: Optional[datetime], calculated_at: Optional[datetime]) -> int:
    if deadline is None:
        return 4
    if calculated_at is None:
        return 3
    delta = deadline - calculated_at
    if delta <= timedelta(0):
        return 0
    if delta <= timedelta(days=1):
        return 1
    if delta <= timedelta(days=7):
        return 2
    return 3


def _dependent_counts(todos: Dict[str, Dict[str, Any]]) -> Dict[str, int]:
    direct: Dict[str, Set[str]] = defaultdict(set)
    for todo_id, todo in todos.items():
        if todo.get("status") in TERMINAL_STATUSES:
            continue
        for predecessor in todo.get("depends_on_todo_ids", []):
            if predecessor in todos:
                direct[predecessor].add(todo_id)

    def descendants(todo_id: str, trail: Set[str]) -> Set[str]:
        found: Set[str] = set()
        for dependent in direct.get(todo_id, set()):
            if dependent in trail:
                continue
            found.add(dependent)
            found.update(descendants(dependent, trail | {dependent}))
        return found

    return {todo_id: len(descendants(todo_id, {todo_id})) for todo_id in todos}


def _reason(signal: Dict[str, Any]) -> str:
    parts: List[str] = []
    urgency = signal["urgency"]
    if urgency == 0:
        parts.append("有效 DDL 已到")
    elif urgency == 1:
        parts.append("有效 DDL 在 24 小时内")
    elif urgency == 2:
        parts.append("有效 DDL 在 7 天内")
    if signal["dependent_count"]:
        parts.append(f"解锁 {signal['dependent_count']} 个后续行动")
    priority = signal["priority_label"]
    parts.append(f"所属目标最高优先级为 {priority}" if priority else "独立行动按正常重要性处理")
    return "；".join(parts)


def _topological_order(
    entries: List[Dict[str, Any]], todos: Dict[str, Dict[str, Any]], keys: Dict[str, Tuple[Any, ...]]
) -> List[Dict[str, Any]]:
    entries_by_todo: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        entries_by_todo[entry.get("todo_id")].append(entry)
    predecessors: Dict[str, Set[str]] = {entry["id"]: set() for entry in entries}
    successors: Dict[str, Set[str]] = {entry["id"]: set() for entry in entries}
    for entry in entries:
        todo = todos.get(entry.get("todo_id"), {})
        for predecessor_todo_id in todo.get("depends_on_todo_ids", []):
            for predecessor_entry in entries_by_todo.get(predecessor_todo_id, []):
                predecessors[entry["id"]].add(predecessor_entry["id"])
                successors[predecessor_entry["id"]].add(entry["id"])

    by_id = {entry["id"]: entry for entry in entries}
    ready = [entry_id for entry_id, required in predecessors.items() if not required]
    result: List[Dict[str, Any]] = []
    while ready:
        ready.sort(key=lambda entry_id: keys[entry_id])
        entry_id = ready.pop(0)
        result.append(by_id[entry_id])
        for successor in successors[entry_id]:
            predecessors[successor].discard(entry_id)
            if not predecessors[successor] and successor not in ready:
                ready.append(successor)
    if len(result) != len(entries):
        return sorted(entries, key=lambda entry: keys[entry["id"]])
    return result


def derive_daily_rankings(plan: Dict[str, Any], calculated_at: Any = None) -> Dict[str, Any]:
    """Return deterministic assignments without mutating *plan*.

    The public result contains ``assignments`` keyed by ScheduleEntry id and
    ``days`` keyed by plan-timezone date. Time is the final stable tie-breaker,
    never the leading ranking rule.
    """

    timezone = ZoneInfo(plan["plan_meta"]["timezone"])
    now = parse_datetime(calculated_at)
    structure = plan.get("task_structure", {})
    goals = _indexed(structure.get("goals", []))
    nodes = _indexed(structure.get("nodes", []))
    todos = _indexed(structure.get("todos", []))
    dependent_counts = _dependent_counts(todos)
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    signals: Dict[str, Dict[str, Any]] = {}
    keys: Dict[str, Tuple[Any, ...]] = {}

    for entry in plan.get("schedule", {}).get("entries", []):
        if not isinstance(entry, dict) or entry.get("source_type") != "todo":
            continue
        starts = parse_datetime(entry.get("starts_at"))
        if starts is None or not isinstance(entry.get("id"), str):
            continue
        todo = todos.get(entry.get("todo_id"), {})
        parents = _active_parents(todo, goals, nodes)
        deadline = _effective_deadline(todo, parents)
        priority_values = [PRIORITY_ORDER.get(goal.get("priority"), 1) for goal, _ in parents]
        priority = min(priority_values) if priority_values else 1
        priority_label = next((label for label, value in PRIORITY_ORDER.items() if value == priority), None) if parents else None
        window_end = parse_datetime((todo.get("execution_window") or {}).get("ends_at"))
        urgency = _urgency(deadline, now)
        signal = {
            "urgency": urgency,
            "effective_deadline": deadline,
            "dependent_count": dependent_counts.get(todo.get("id"), 0),
            "priority": priority,
            "priority_label": priority_label,
            "window_end": window_end,
        }
        signals[entry["id"]] = signal
        keys[entry["id"]] = (
            urgency,
            deadline or datetime.max.replace(tzinfo=timezone),
            -signal["dependent_count"],
            priority,
            window_end or datetime.max.replace(tzinfo=timezone),
            starts,
            entry["id"],
        )
        groups[starts.astimezone(timezone).date().isoformat()].append(entry)

    assignments: Dict[str, Dict[str, Any]] = {}
    days: Dict[str, List[Dict[str, Any]]] = {}
    for day, entries in sorted(groups.items()):
        ordered = _topological_order(entries, todos, keys)
        days[day] = []
        for rank, entry in enumerate(ordered, 1):
            reason = _reason(signals[entry["id"]])
            assignment = {"daily_rank": rank, "priority_reason": reason}
            assignments[entry["id"]] = assignment
            days[day].append({"entry_id": entry["id"], **assignment})
    return {"assignments": assignments, "days": days}
