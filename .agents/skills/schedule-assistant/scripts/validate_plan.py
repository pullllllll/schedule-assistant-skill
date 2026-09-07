#!/usr/bin/env python3
"""Validate a complete schedule-assistant plan without modifying it."""

from __future__ import annotations

import argparse
import calendar
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from daily_ranking import derive_daily_rankings


TOP_LEVEL_FIELDS = {
    "plan_meta",
    "task_structure",
    "time_constraints",
    "availability_rules",
    "schedule",
}
RFC3339_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
GOAL_FIELDS = {
    "id", "title", "type", "status", "priority", "outcome", "deadline",
    "recurrence", "cycle_template", "habit_rule", "context", "created_at", "updated_at",
}
NODE_FIELDS = {
    "id", "goal_id", "title", "outcome", "status", "starts_on", "deadline",
    "depends_on_node_ids", "context", "created_at", "updated_at",
}
TODO_FIELDS = {
    "id", "title", "parent_refs", "status", "deadline", "execution_window",
    "remaining_estimate_minutes", "execution_mode", "intensity", "depends_on_todo_ids",
    "context", "created_at", "updated_at",
}
TIME_CONSTRAINT_FIELDS = {
    "id", "kind", "title", "status", "starts_at", "ends_at", "recurrence",
    "context", "created_at", "updated_at",
}


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def parse_datetime_value(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not RFC3339_PATTERN.fullmatch(value):
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def parse_date_value(value: Any) -> Optional[date]:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def parse_time_value(value: Any) -> Optional[time]:
    if not isinstance(value, str):
        return None
    try:
        parsed = time.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is not None or len(value) != 5:
        return None
    return parsed


def add_months(anchor: date, months: int) -> date:
    month_index = anchor.year * 12 + anchor.month - 1 + months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(anchor.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def shift_local_datetime(anchor: datetime, unit: str, amount: int, timezone: ZoneInfo) -> datetime:
    local = anchor.astimezone(timezone)
    if unit == "day":
        target_date = local.date() + timedelta(days=amount)
    elif unit == "week":
        target_date = local.date() + timedelta(weeks=amount)
    else:
        target_date = add_months(local.date(), amount)
    return datetime.combine(target_date, local.timetz().replace(tzinfo=None), timezone)


def duration_minutes(starts_at: datetime, ends_at: datetime) -> int:
    return int((ends_at - starts_at).total_seconds() // 60)


class Validator:
    def __init__(
        self,
        plan: Any,
        calculated_at: Optional[datetime],
        date_classifications: Dict[str, str],
    ) -> None:
        self.plan = plan
        self.requested_calculated_at = calculated_at
        self.date_classifications = date_classifications
        self.errors: List[Dict[str, str]] = []
        self.warnings: List[Dict[str, str]] = []
        self.timezone: Optional[ZoneInfo] = None
        self.calculated_at: Optional[datetime] = None
        self.goals: Dict[str, Dict[str, Any]] = {}
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.todos: Dict[str, Dict[str, Any]] = {}
        self.time_constraints: Dict[str, Dict[str, Any]] = {}
        self.schedule_entries: List[Dict[str, Any]] = []
        self.entry_times: Dict[str, Tuple[datetime, datetime]] = {}

    def issue(self, level: str, code: str, path: str, message: str) -> None:
        item = {"code": code, "path": path, "message": message}
        (self.errors if level == "error" else self.warnings).append(item)

    def error(self, code: str, path: str, message: str) -> None:
        self.issue("error", code, path, message)

    def warning(self, code: str, path: str, message: str) -> None:
        self.issue("warning", code, path, message)

    def object_fields(self, value: Any, path: str, fields: Set[str]) -> bool:
        if not isinstance(value, dict):
            self.error("invalid_type", path, "must be an object")
            return False
        missing = sorted(fields - set(value))
        unknown = sorted(set(value) - fields)
        for name in missing:
            self.error("missing_field", f"{path}.{name}", "required field is missing")
        for name in unknown:
            self.error("unknown_field", f"{path}.{name}", "field is not part of the plan contract")
        return not missing

    def array(self, value: Any, path: str) -> bool:
        if not isinstance(value, list):
            self.error("invalid_type", path, "must be an array")
            return False
        return True

    def string(self, value: Any, path: str, nullable: bool = False) -> bool:
        if nullable and value is None:
            return True
        if not isinstance(value, str) or not value.strip():
            self.error("invalid_string", path, "must be a non-empty string" if not nullable else "must be a non-empty string or null")
            return False
        return True

    def enum(self, value: Any, path: str, allowed: Set[str]) -> bool:
        if value not in allowed:
            self.error("invalid_enum", path, f"must be one of {sorted(allowed)}")
            return False
        return True

    def integer(self, value: Any, path: str, minimum: int = 0) -> bool:
        if not is_int(value) or value < minimum:
            self.error("invalid_integer", path, f"must be an integer >= {minimum}")
            return False
        return True

    def datetime(self, value: Any, path: str, nullable: bool = False) -> Optional[datetime]:
        if nullable and value is None:
            return None
        parsed = parse_datetime_value(value)
        if parsed is None:
            self.error("invalid_datetime", path, "must be an RFC 3339 datetime with a UTC offset")
        return parsed

    def date(self, value: Any, path: str, nullable: bool = False) -> Optional[date]:
        if nullable and value is None:
            return None
        parsed = parse_date_value(value)
        if parsed is None:
            self.error("invalid_date", path, "must use YYYY-MM-DD")
        return parsed

    def time(self, value: Any, path: str) -> Optional[time]:
        parsed = parse_time_value(value)
        if parsed is None:
            self.error("invalid_time", path, "must use local HH:MM")
        return parsed

    def timestamps(self, value: Dict[str, Any], path: str) -> None:
        created = self.datetime(value.get("created_at"), f"{path}.created_at")
        updated = self.datetime(value.get("updated_at"), f"{path}.updated_at")
        if created and updated and updated < created:
            self.error("timestamp_order", f"{path}.updated_at", "must not be earlier than created_at")

    def validate(self) -> Dict[str, Any]:
        if not self.object_fields(self.plan, "$", TOP_LEVEL_FIELDS):
            return self.result()
        self.validate_meta()
        self.validate_goals()
        self.validate_nodes()
        self.validate_todos()
        self.validate_time_constraints()
        self.validate_availability()
        self.validate_schedule()
        return self.result()

    def result(self) -> Dict[str, Any]:
        return {
            "ok": not self.errors,
            "errors": self.errors,
            "warnings": self.warnings,
            "summary": {"error_count": len(self.errors), "warning_count": len(self.warnings)},
        }

    def validate_meta(self) -> None:
        meta = self.plan.get("plan_meta")
        path = "$.plan_meta"
        if not self.object_fields(meta, path, {"plan_version", "timezone", "progress_reviewed_through", "updated_at"}):
            return
        self.integer(meta.get("plan_version"), f"{path}.plan_version")
        timezone_name = meta.get("timezone")
        if self.string(timezone_name, f"{path}.timezone"):
            try:
                self.timezone = ZoneInfo(timezone_name)
            except ZoneInfoNotFoundError:
                self.error("invalid_timezone", f"{path}.timezone", "must be a known IANA timezone")
        self.datetime(meta.get("progress_reviewed_through"), f"{path}.progress_reviewed_through", nullable=True)
        self.datetime(meta.get("updated_at"), f"{path}.updated_at")
        if self.timezone:
            self.calculated_at = (
                self.requested_calculated_at.astimezone(self.timezone)
                if self.requested_calculated_at
                else datetime.now(self.timezone)
            )

    def unique_id(self, value: Any, path: str, target: Dict[str, Dict[str, Any]], obj: Dict[str, Any]) -> None:
        if not self.string(value, path):
            return
        if value in target:
            self.error("duplicate_id", path, f"duplicate id: {value}")
        else:
            target[value] = obj

    def validate_goals(self) -> None:
        structure = self.plan.get("task_structure")
        if not self.object_fields(structure, "$.task_structure", {"goals", "nodes", "todos"}):
            return
        goals = structure.get("goals")
        if not self.array(goals, "$.task_structure.goals"):
            return
        for index, goal in enumerate(goals):
            path = f"$.task_structure.goals[{index}]"
            if not self.object_fields(goal, path, GOAL_FIELDS):
                continue
            self.unique_id(goal.get("id"), f"{path}.id", self.goals, goal)
            self.string(goal.get("title"), f"{path}.title")
            goal_type = goal.get("type")
            self.enum(goal_type, f"{path}.type", {"linear", "recurring", "habit"})
            status = goal.get("status")
            self.enum(status, f"{path}.status", {"active", "paused", "completed", "cancelled"})
            if goal_type in {"recurring", "habit"} and status == "completed":
                self.error("invalid_goal_status", f"{path}.status", "recurring and habit goals cannot be completed")
            self.enum(goal.get("priority"), f"{path}.priority", {"P0", "P1", "P2"})
            self.string(goal.get("outcome"), f"{path}.outcome")
            self.string(goal.get("context"), f"{path}.context", nullable=True)
            self.timestamps(goal, path)
            deadline = self.datetime(goal.get("deadline"), f"{path}.deadline", nullable=True)
            if goal_type != "linear" and goal.get("deadline") is not None:
                self.error("goal_type_fields", f"{path}.deadline", "only linear goals may have a deadline")
            if goal_type == "linear":
                for field in ("recurrence", "cycle_template", "habit_rule"):
                    if goal.get(field) is not None:
                        self.error("goal_type_fields", f"{path}.{field}", "must be null for a linear goal")
            elif goal_type == "recurring":
                if goal.get("habit_rule") is not None:
                    self.error("goal_type_fields", f"{path}.habit_rule", "must be null for a recurring goal")
                self.validate_goal_recurrence(goal.get("recurrence"), f"{path}.recurrence")
                self.validate_cycle_template(goal.get("cycle_template"), f"{path}.cycle_template")
            elif goal_type == "habit":
                for field in ("recurrence", "cycle_template"):
                    if goal.get(field) is not None:
                        self.error("goal_type_fields", f"{path}.{field}", "must be null for a habit goal")
                self.validate_habit_rule(goal.get("habit_rule"), f"{path}.habit_rule")

    def validate_goal_recurrence(self, value: Any, path: str) -> None:
        fields = {"interval", "unit", "first_cycle_starts_on", "first_deadline"}
        if not self.object_fields(value, path, fields):
            return
        self.integer(value.get("interval"), f"{path}.interval", 1)
        self.enum(value.get("unit"), f"{path}.unit", {"week", "month"})
        self.date(value.get("first_cycle_starts_on"), f"{path}.first_cycle_starts_on")
        self.datetime(value.get("first_deadline"), f"{path}.first_deadline")

    def validate_cycle_template(self, value: Any, path: str) -> None:
        if not self.object_fields(value, path, {"node", "todos"}):
            return
        node = value.get("node")
        if self.object_fields(node, f"{path}.node", {"title", "outcome", "context"}):
            self.string(node.get("title"), f"{path}.node.title")
            self.string(node.get("outcome"), f"{path}.node.outcome")
            self.string(node.get("context"), f"{path}.node.context", nullable=True)
        todos = value.get("todos")
        if not self.array(todos, f"{path}.todos"):
            return
        keys: Dict[str, Dict[str, Any]] = {}
        graph: Dict[str, List[str]] = {}
        for index, todo in enumerate(todos):
            todo_path = f"{path}.todos[{index}]"
            fields = {"template_key", "title", "estimate_minutes", "execution_mode", "intensity", "depends_on_template_keys", "context"}
            if not self.object_fields(todo, todo_path, fields):
                continue
            key = todo.get("template_key")
            self.unique_id(key, f"{todo_path}.template_key", keys, todo)
            self.string(todo.get("title"), f"{todo_path}.title")
            self.integer(todo.get("estimate_minutes"), f"{todo_path}.estimate_minutes", 1)
            self.enum(todo.get("execution_mode"), f"{todo_path}.execution_mode", {"single_block", "splittable"})
            self.enum(todo.get("intensity"), f"{todo_path}.intensity", {"low", "medium", "high"})
            self.string(todo.get("context"), f"{todo_path}.context", nullable=True)
            dependencies = todo.get("depends_on_template_keys")
            if self.array(dependencies, f"{todo_path}.depends_on_template_keys") and isinstance(key, str):
                graph[key] = []
                seen: Set[str] = set()
                for dep_index, dependency in enumerate(dependencies):
                    dep_path = f"{todo_path}.depends_on_template_keys[{dep_index}]"
                    if self.string(dependency, dep_path):
                        if dependency in seen:
                            self.error("duplicate_reference", dep_path, "template dependency is duplicated")
                        seen.add(dependency)
                        graph[key].append(dependency)
        for key, dependencies in graph.items():
            for dependency in dependencies:
                if dependency not in keys:
                    self.error("missing_reference", path, f"template {key} depends on missing template {dependency}")
        self.validate_acyclic(graph, path, "template_dependency_cycle")

    def validate_habit_rule(self, value: Any, path: str) -> None:
        fields = {"interval", "unit", "first_period_starts_on", "minimum_count", "session_minutes", "spacing_rule", "preferred_windows"}
        if not self.object_fields(value, path, fields):
            return
        self.integer(value.get("interval"), f"{path}.interval", 1)
        self.enum(value.get("unit"), f"{path}.unit", {"day", "week", "month"})
        self.date(value.get("first_period_starts_on"), f"{path}.first_period_starts_on")
        self.integer(value.get("minimum_count"), f"{path}.minimum_count", 1)
        self.integer(value.get("session_minutes"), f"{path}.session_minutes", 1)
        spacing = value.get("spacing_rule")
        if spacing is not None and self.object_fields(spacing, f"{path}.spacing_rule", {"min_rest_days", "strictness"}):
            self.integer(spacing.get("min_rest_days"), f"{path}.spacing_rule.min_rest_days")
            self.enum(spacing.get("strictness"), f"{path}.spacing_rule.strictness", {"preferred", "required"})
        windows = value.get("preferred_windows")
        if self.array(windows, f"{path}.preferred_windows"):
            for index, window in enumerate(windows):
                window_path = f"{path}.preferred_windows[{index}]"
                if not self.object_fields(window, window_path, {"weekdays", "starts_time", "ends_time", "strictness"}):
                    continue
                weekdays = window.get("weekdays")
                if weekdays is not None:
                    if self.array(weekdays, f"{window_path}.weekdays"):
                        seen: Set[int] = set()
                        for day_index, weekday in enumerate(weekdays):
                            day_path = f"{window_path}.weekdays[{day_index}]"
                            if not is_int(weekday) or not 1 <= weekday <= 7:
                                self.error("invalid_weekday", day_path, "must be an ISO weekday from 1 to 7")
                            elif weekday in seen:
                                self.error("duplicate_weekday", day_path, "weekday is duplicated")
                            seen.add(weekday)
                starts = self.time(window.get("starts_time"), f"{window_path}.starts_time")
                ends = self.time(window.get("ends_time"), f"{window_path}.ends_time")
                if starts and ends and ends <= starts:
                    self.error("time_order", f"{window_path}.ends_time", "must be later than starts_time")
                self.enum(window.get("strictness"), f"{window_path}.strictness", {"preferred", "required"})

    def validate_nodes(self) -> None:
        structure = self.plan.get("task_structure")
        nodes = structure.get("nodes") if isinstance(structure, dict) else None
        if not self.array(nodes, "$.task_structure.nodes"):
            return
        graph: Dict[str, List[str]] = {}
        recurring_identities: Set[Tuple[str, str]] = set()
        for index, node in enumerate(nodes):
            path = f"$.task_structure.nodes[{index}]"
            if not self.object_fields(node, path, NODE_FIELDS):
                continue
            node_id = node.get("id")
            self.unique_id(node_id, f"{path}.id", self.nodes, node)
            goal_id = node.get("goal_id")
            self.string(goal_id, f"{path}.goal_id")
            if isinstance(goal_id, str) and goal_id not in self.goals:
                self.error("missing_reference", f"{path}.goal_id", "referenced goal does not exist")
            self.string(node.get("title"), f"{path}.title")
            self.string(node.get("outcome"), f"{path}.outcome")
            self.enum(node.get("status"), f"{path}.status", {"pending", "in_progress", "completed", "cancelled"})
            starts_on = self.date(node.get("starts_on"), f"{path}.starts_on", nullable=True)
            deadline = self.datetime(node.get("deadline"), f"{path}.deadline", nullable=True)
            self.string(node.get("context"), f"{path}.context", nullable=True)
            self.timestamps(node, path)
            goal = self.goals.get(goal_id) if isinstance(goal_id, str) else None
            if goal and goal.get("type") == "recurring":
                if starts_on is None:
                    self.error("recurring_node_fields", f"{path}.starts_on", "recurring nodes require starts_on")
                if deadline is None:
                    self.error("recurring_node_fields", f"{path}.deadline", "recurring nodes require a deadline")
                if starts_on:
                    identity = (goal_id, starts_on.isoformat())
                    if identity in recurring_identities:
                        self.error("duplicate_recurring_node", path, "recurring node identity (goal_id, starts_on) is duplicated")
                    recurring_identities.add(identity)
            dependencies = node.get("depends_on_node_ids")
            if self.array(dependencies, f"{path}.depends_on_node_ids") and isinstance(node_id, str):
                graph[node_id] = []
                seen: Set[str] = set()
                for dep_index, dependency in enumerate(dependencies):
                    dep_path = f"{path}.depends_on_node_ids[{dep_index}]"
                    if self.string(dependency, dep_path):
                        if dependency == node_id:
                            self.error("self_dependency", dep_path, "node cannot depend on itself")
                        if dependency in seen:
                            self.error("duplicate_reference", dep_path, "node dependency is duplicated")
                        seen.add(dependency)
                        graph[node_id].append(dependency)
        for node_id, dependencies in graph.items():
            for dependency in dependencies:
                if dependency not in self.nodes:
                    self.error("missing_reference", f"$.task_structure.nodes[{node_id}]", f"depends on missing node {dependency}")
        self.validate_acyclic(graph, "$.task_structure.nodes", "node_dependency_cycle")

    def validate_todos(self) -> None:
        structure = self.plan.get("task_structure")
        todos = structure.get("todos") if isinstance(structure, dict) else None
        if not self.array(todos, "$.task_structure.todos"):
            return
        graph: Dict[str, List[str]] = {}
        habit_identities: Set[Tuple[str, str, int]] = set()
        for index, todo in enumerate(todos):
            path = f"$.task_structure.todos[{index}]"
            if not self.object_fields(todo, path, TODO_FIELDS):
                continue
            todo_id = todo.get("id")
            self.unique_id(todo_id, f"{path}.id", self.todos, todo)
            self.string(todo.get("title"), f"{path}.title")
            status = todo.get("status")
            self.enum(status, f"{path}.status", {"pending", "in_progress", "completed", "cancelled"})
            self.datetime(todo.get("deadline"), f"{path}.deadline", nullable=True)
            remaining = todo.get("remaining_estimate_minutes")
            if self.integer(remaining, f"{path}.remaining_estimate_minutes"):
                if status in {"completed", "cancelled"} and remaining != 0:
                    self.error("terminal_remaining_work", f"{path}.remaining_estimate_minutes", "completed or cancelled todo must have zero remaining work")
                if status in {"pending", "in_progress"} and remaining == 0:
                    self.warning("zero_remaining_nonterminal", f"{path}.remaining_estimate_minutes", "active todo has no remaining work but is not terminal")
            self.enum(todo.get("execution_mode"), f"{path}.execution_mode", {"single_block", "splittable"})
            self.enum(todo.get("intensity"), f"{path}.intensity", {"low", "medium", "high"})
            self.string(todo.get("context"), f"{path}.context", nullable=True)
            self.timestamps(todo, path)
            window = todo.get("execution_window")
            if self.object_fields(window, f"{path}.execution_window", {"starts_at", "ends_at"}):
                starts = self.datetime(window.get("starts_at"), f"{path}.execution_window.starts_at")
                ends = self.datetime(window.get("ends_at"), f"{path}.execution_window.ends_at")
                if starts and ends and ends <= starts:
                    self.error("time_order", f"{path}.execution_window.ends_at", "must be later than starts_at")
            self.validate_parent_refs(todo.get("parent_refs"), f"{path}.parent_refs", habit_identities)
            dependencies = todo.get("depends_on_todo_ids")
            if self.array(dependencies, f"{path}.depends_on_todo_ids") and isinstance(todo_id, str):
                graph[todo_id] = []
                seen: Set[str] = set()
                for dep_index, dependency in enumerate(dependencies):
                    dep_path = f"{path}.depends_on_todo_ids[{dep_index}]"
                    if self.string(dependency, dep_path):
                        if dependency == todo_id:
                            self.error("self_dependency", dep_path, "todo cannot depend on itself")
                        if dependency in seen:
                            self.error("duplicate_reference", dep_path, "todo dependency is duplicated")
                        seen.add(dependency)
                        graph[todo_id].append(dependency)
        for todo_id, dependencies in graph.items():
            for dependency in dependencies:
                if dependency not in self.todos:
                    self.error("missing_reference", f"$.task_structure.todos[{todo_id}]", f"depends on missing todo {dependency}")
        self.validate_acyclic(graph, "$.task_structure.todos", "todo_dependency_cycle")

    def validate_parent_refs(self, value: Any, path: str, habit_identities: Set[Tuple[str, str, int]]) -> None:
        if not self.array(value, path):
            return
        normal_seen: Set[Tuple[str, Optional[str]]] = set()
        for index, parent in enumerate(value):
            parent_path = f"{path}[{index}]"
            fields = {"goal_id", "node_id", "habit_period_starts_on", "habit_occurrence_index"}
            if not self.object_fields(parent, parent_path, fields):
                continue
            goal_id = parent.get("goal_id")
            node_id = parent.get("node_id")
            period_raw = parent.get("habit_period_starts_on")
            occurrence = parent.get("habit_occurrence_index")
            self.string(goal_id, f"{parent_path}.goal_id")
            goal = self.goals.get(goal_id) if isinstance(goal_id, str) else None
            if isinstance(goal_id, str) and goal is None:
                self.error("missing_reference", f"{parent_path}.goal_id", "referenced goal does not exist")
            if node_id is not None:
                self.string(node_id, f"{parent_path}.node_id")
            if goal and goal.get("type") == "habit":
                if node_id is not None:
                    self.error("habit_parent_fields", f"{parent_path}.node_id", "habit parent must reference the goal directly")
                period = self.date(period_raw, f"{parent_path}.habit_period_starts_on")
                if not is_int(occurrence) or occurrence < 1:
                    self.error("habit_parent_fields", f"{parent_path}.habit_occurrence_index", "must be an integer >= 1")
                if period and not self.valid_habit_period(goal, period):
                    self.error("invalid_habit_period", f"{parent_path}.habit_period_starts_on", "does not align with the habit period anchor")
                if period and is_int(occurrence) and occurrence >= 1:
                    identity = (goal_id, period.isoformat(), occurrence)
                    if identity in habit_identities:
                        self.error("duplicate_habit_identity", parent_path, "habit occurrence identity is already used by another todo")
                    habit_identities.add(identity)
            else:
                if period_raw is not None or occurrence is not None:
                    self.error("normal_parent_fields", parent_path, "non-habit parent must have null habit fields")
                identity = (goal_id, node_id)
                if identity in normal_seen:
                    self.error("duplicate_parent_ref", parent_path, "normal parent reference is duplicated")
                normal_seen.add(identity)
                if isinstance(node_id, str):
                    node = self.nodes.get(node_id)
                    if node is None:
                        self.error("missing_reference", f"{parent_path}.node_id", "referenced node does not exist")
                    elif node.get("goal_id") != goal_id:
                        self.error("parent_goal_mismatch", parent_path, "node does not belong to parent goal")

    def valid_habit_period(self, goal: Dict[str, Any], candidate: date) -> bool:
        rule = goal.get("habit_rule")
        if not isinstance(rule, dict):
            return False
        anchor = parse_date_value(rule.get("first_period_starts_on"))
        interval = rule.get("interval")
        unit = rule.get("unit")
        if not anchor or not is_int(interval) or interval <= 0 or candidate < anchor:
            return False
        if unit == "day":
            return (candidate - anchor).days % interval == 0
        if unit == "week":
            return (candidate - anchor).days % (7 * interval) == 0
        if unit == "month":
            delta = (candidate.year - anchor.year) * 12 + candidate.month - anchor.month
            return delta >= 0 and delta % interval == 0 and add_months(anchor, delta) == candidate
        return False

    def validate_acyclic(self, graph: Dict[str, List[str]], path: str, code: str) -> None:
        visiting: Set[str] = set()
        visited: Set[str] = set()

        def visit(node: str, trail: List[str]) -> None:
            if node in visiting:
                start = trail.index(node) if node in trail else 0
                cycle = trail[start:] + [node]
                self.error(code, path, "dependency cycle: " + " -> ".join(cycle))
                return
            if node in visited:
                return
            visiting.add(node)
            for dependency in graph.get(node, []):
                if dependency in graph:
                    visit(dependency, trail + [node])
            visiting.remove(node)
            visited.add(node)

        for node in graph:
            visit(node, [])

    def validate_time_constraints(self) -> None:
        constraints = self.plan.get("time_constraints")
        if not self.array(constraints, "$.time_constraints"):
            return
        for index, constraint in enumerate(constraints):
            path = f"$.time_constraints[{index}]"
            if not self.object_fields(constraint, path, TIME_CONSTRAINT_FIELDS):
                continue
            constraint_id = constraint.get("id")
            self.unique_id(constraint_id, f"{path}.id", self.time_constraints, constraint)
            self.enum(constraint.get("kind"), f"{path}.kind", {"commitment", "unavailable"})
            self.string(constraint.get("title"), f"{path}.title")
            self.enum(constraint.get("status"), f"{path}.status", {"active", "cancelled"})
            starts = self.datetime(constraint.get("starts_at"), f"{path}.starts_at")
            ends = self.datetime(constraint.get("ends_at"), f"{path}.ends_at")
            if starts and ends and ends <= starts:
                self.error("time_order", f"{path}.ends_at", "must be later than starts_at")
            self.string(constraint.get("context"), f"{path}.context", nullable=True)
            self.timestamps(constraint, path)
            recurrence = constraint.get("recurrence")
            if recurrence is not None:
                self.validate_time_constraint_recurrence(recurrence, f"{path}.recurrence", starts)

    def validate_time_constraint_recurrence(self, value: Any, path: str, anchor: Optional[datetime]) -> None:
        fields = {"interval", "unit", "ends_on", "exceptions"}
        if not self.object_fields(value, path, fields):
            return
        self.integer(value.get("interval"), f"{path}.interval", 1)
        self.enum(value.get("unit"), f"{path}.unit", {"day", "week", "month"})
        ends_on = self.date(value.get("ends_on"), f"{path}.ends_on", nullable=True)
        if ends_on and anchor and self.timezone and ends_on < anchor.astimezone(self.timezone).date():
            self.error("invalid_recurrence_end", f"{path}.ends_on", "must not be earlier than the first occurrence date")
        exceptions = value.get("exceptions")
        if not self.array(exceptions, f"{path}.exceptions"):
            return
        seen: Set[datetime] = set()
        for index, exception in enumerate(exceptions):
            exception_path = f"{path}.exceptions[{index}]"
            if not isinstance(exception, dict):
                self.error("invalid_type", exception_path, "must be an object")
                continue
            action = exception.get("action")
            expected_fields = {"occurrence_starts_at", "action"} if action == "skip" else {"occurrence_starts_at", "action", "starts_at", "ends_at"}
            if not self.object_fields(exception, exception_path, expected_fields):
                continue
            occurrence = self.datetime(exception.get("occurrence_starts_at"), f"{exception_path}.occurrence_starts_at")
            self.enum(action, f"{exception_path}.action", {"skip", "reschedule"})
            if occurrence:
                if occurrence in seen:
                    self.error("duplicate_exception", f"{exception_path}.occurrence_starts_at", "only one exception is allowed per occurrence")
                seen.add(occurrence)
            if action == "reschedule":
                starts = self.datetime(exception.get("starts_at"), f"{exception_path}.starts_at")
                ends = self.datetime(exception.get("ends_at"), f"{exception_path}.ends_at")
                if starts and ends and ends <= starts:
                    self.error("time_order", f"{exception_path}.ends_at", "must be later than starts_at")
            if occurrence and anchor and self.timezone:
                if not self.is_base_occurrence(value, anchor, occurrence):
                    self.error("invalid_exception_key", f"{exception_path}.occurrence_starts_at", "does not match an occurrence generated from the recurrence anchor")
                elif ends_on and occurrence.astimezone(self.timezone).date() > ends_on:
                    self.error("invalid_exception_key", f"{exception_path}.occurrence_starts_at", "occurrence is later than recurrence.ends_on")

    def is_base_occurrence(self, recurrence: Dict[str, Any], anchor: datetime, candidate: datetime) -> bool:
        if not self.timezone:
            return False
        interval = recurrence.get("interval")
        unit = recurrence.get("unit")
        if not is_int(interval) or interval <= 0 or unit not in {"day", "week", "month"}:
            return False
        anchor_local = anchor.astimezone(self.timezone)
        candidate_local = candidate.astimezone(self.timezone)
        if candidate_local.timetz().replace(tzinfo=None) != anchor_local.timetz().replace(tzinfo=None):
            return False
        if candidate < anchor:
            return False
        if unit == "day":
            days = (candidate_local.date() - anchor_local.date()).days
            amount = days
        elif unit == "week":
            days = (candidate_local.date() - anchor_local.date()).days
            if days % 7:
                return False
            amount = days // 7
        else:
            amount = (candidate_local.year - anchor_local.year) * 12 + candidate_local.month - anchor_local.month
        if amount < 0 or amount % interval:
            return False
        expected = shift_local_datetime(anchor, unit, amount, self.timezone)
        return expected == candidate

    def validate_availability(self) -> None:
        value = self.plan.get("availability_rules")
        path = "$.availability_rules"
        if not self.object_fields(value, path, {"calendar_id", "workday", "holiday", "date_overrides"}):
            return
        self.string(value.get("calendar_id"), f"{path}.calendar_id", nullable=True)
        self.validate_availability_profile(value.get("workday"), f"{path}.workday")
        self.validate_availability_profile(value.get("holiday"), f"{path}.holiday")
        if isinstance(value.get("workday"), dict) and isinstance(value.get("holiday"), dict):
            workday_id = value["workday"].get("id")
            holiday_id = value["holiday"].get("id")
            if isinstance(workday_id, str) and workday_id == holiday_id:
                self.error("duplicate_id", f"{path}.holiday.id", "availability profile ids must be unique")
        overrides = value.get("date_overrides")
        if not self.array(overrides, f"{path}.date_overrides"):
            return
        override_ids: Set[str] = set()
        ranges: List[Tuple[date, date, str]] = []
        for index, override in enumerate(overrides):
            override_path = f"{path}.date_overrides[{index}]"
            fields = {"id", "starts_on", "ends_on", "segments", "context", "created_at", "updated_at"}
            if not self.object_fields(override, override_path, fields):
                continue
            override_id = override.get("id")
            if self.string(override_id, f"{override_path}.id"):
                if override_id in override_ids:
                    self.error("duplicate_id", f"{override_path}.id", "date override id is duplicated")
                override_ids.add(override_id)
            starts = self.date(override.get("starts_on"), f"{override_path}.starts_on")
            ends = self.date(override.get("ends_on"), f"{override_path}.ends_on")
            if starts and ends:
                if ends < starts:
                    self.error("date_order", f"{override_path}.ends_on", "must not be earlier than starts_on")
                else:
                    ranges.append((starts, ends, override_path))
            self.validate_segments(override.get("segments"), f"{override_path}.segments")
            self.string(override.get("context"), f"{override_path}.context", nullable=True)
            self.timestamps(override, override_path)
        ranges.sort()
        for previous, current in zip(ranges, ranges[1:]):
            if current[0] <= previous[1]:
                self.error("overlapping_date_overrides", current[2], f"overlaps {previous[2]}")

    def validate_availability_profile(self, value: Any, path: str) -> None:
        if not self.object_fields(value, path, {"id", "segments", "context", "created_at", "updated_at"}):
            return
        self.string(value.get("id"), f"{path}.id")
        self.validate_segments(value.get("segments"), f"{path}.segments")
        self.string(value.get("context"), f"{path}.context", nullable=True)
        self.timestamps(value, path)

    def validate_segments(self, value: Any, path: str) -> None:
        if not self.array(value, path):
            return
        ranges: List[Tuple[time, time, str]] = []
        for index, segment in enumerate(value):
            segment_path = f"{path}[{index}]"
            if not self.object_fields(segment, segment_path, {"starts_time", "ends_time", "energy_level"}):
                continue
            starts = self.time(segment.get("starts_time"), f"{segment_path}.starts_time")
            ends = self.time(segment.get("ends_time"), f"{segment_path}.ends_time")
            self.enum(segment.get("energy_level"), f"{segment_path}.energy_level", {"low", "medium", "high"})
            if starts and ends:
                if ends <= starts:
                    self.error("time_order", f"{segment_path}.ends_time", "must be later than starts_time")
                else:
                    ranges.append((starts, ends, segment_path))
        ranges.sort()
        for previous, current in zip(ranges, ranges[1:]):
            if current[0] < previous[1]:
                self.error("overlapping_availability_segments", current[2], f"overlaps {previous[2]}")

    def validate_schedule(self) -> None:
        schedule = self.plan.get("schedule")
        path = "$.schedule"
        if not self.object_fields(schedule, path, {"planning_window", "generated_at", "entries"}):
            return
        window = schedule.get("planning_window")
        window_dates: Optional[Tuple[date, date]] = None
        if window is None:
            if schedule.get("generated_at") is not None:
                self.error("empty_schedule_state", f"{path}.generated_at", "must be null when planning_window is null")
        elif self.object_fields(window, f"{path}.planning_window", {"starts_on", "ends_on"}):
            starts_on = self.date(window.get("starts_on"), f"{path}.planning_window.starts_on")
            ends_on = self.date(window.get("ends_on"), f"{path}.planning_window.ends_on")
            if starts_on and ends_on:
                window_dates = (starts_on, ends_on)
                if starts_on.weekday() != 0 or ends_on.weekday() != 6 or (ends_on - starts_on).days != 6:
                    self.error("invalid_planning_window", f"{path}.planning_window", "must be one natural Monday-through-Sunday week")
            if schedule.get("generated_at") is None:
                self.error("missing_generated_at", f"{path}.generated_at", "must be set when planning_window exists")
        self.datetime(schedule.get("generated_at"), f"{path}.generated_at", nullable=True)
        entries = schedule.get("entries")
        if not self.array(entries, f"{path}.entries"):
            return
        if window is None and entries:
            self.error("empty_schedule_state", f"{path}.entries", "must be empty when planning_window is null")
        entry_ids: Set[str] = set()
        for index, entry in enumerate(entries):
            self.validate_schedule_entry(entry, index, entry_ids, window_dates)
        self.validate_schedule_ranks()
        self.validate_schedule_overlaps()
        self.validate_time_constraint_projections(window_dates)
        self.validate_todo_schedule_semantics(window_dates)

    def validate_schedule_entry(
        self,
        entry: Any,
        index: int,
        entry_ids: Set[str],
        window_dates: Optional[Tuple[date, date]],
    ) -> None:
        path = f"$.schedule.entries[{index}]"
        if not isinstance(entry, dict):
            self.error("invalid_type", path, "must be an object")
            return
        source_type = entry.get("source_type")
        if source_type == "todo":
            fields = {"id", "source_type", "starts_at", "ends_at", "todo_id", "daily_rank", "priority_reason", "ranking_exception", "availability_exception"}
        elif source_type == "time_constraint":
            fields = {"id", "source_type", "starts_at", "ends_at", "time_constraint_id"}
        else:
            fields = {"id", "source_type", "starts_at", "ends_at"}
        if not self.object_fields(entry, path, fields):
            return
        entry_id = entry.get("id")
        if self.string(entry_id, f"{path}.id"):
            if entry_id in entry_ids:
                self.error("duplicate_id", f"{path}.id", "ScheduleEntry id is duplicated")
            entry_ids.add(entry_id)
        self.enum(source_type, f"{path}.source_type", {"todo", "time_constraint"})
        starts = self.datetime(entry.get("starts_at"), f"{path}.starts_at")
        ends = self.datetime(entry.get("ends_at"), f"{path}.ends_at")
        if starts and ends:
            if ends <= starts:
                self.error("time_order", f"{path}.ends_at", "must be later than starts_at")
            elif isinstance(entry_id, str):
                self.entry_times[entry_id] = (starts, ends)
            if self.timezone and source_type == "todo":
                if starts.astimezone(self.timezone).date() != (ends - timedelta(microseconds=1)).astimezone(self.timezone).date():
                    self.error("todo_crosses_day", path, "todo ScheduleEntry must not cross a plan-timezone natural day")
            if self.timezone and window_dates and self.calculated_at and starts > self.calculated_at and source_type == "todo":
                local_date = starts.astimezone(self.timezone).date()
                if not window_dates[0] <= local_date <= window_dates[1]:
                    self.error("future_entry_outside_window", path, "future ScheduleEntry must be inside the current planning_window")
        if source_type == "todo":
            todo_id = entry.get("todo_id")
            self.string(todo_id, f"{path}.todo_id")
            if isinstance(todo_id, str) and todo_id not in self.todos:
                self.error("missing_reference", f"{path}.todo_id", "referenced todo does not exist")
            self.integer(entry.get("daily_rank"), f"{path}.daily_rank", 1)
            self.string(entry.get("priority_reason"), f"{path}.priority_reason", nullable=True)
            ranking_exception = entry.get("ranking_exception")
            if ranking_exception is not None and self.object_fields(
                ranking_exception,
                f"{path}.ranking_exception",
                {"confirmed_at", "reason"},
            ):
                self.datetime(ranking_exception.get("confirmed_at"), f"{path}.ranking_exception.confirmed_at")
                if self.string(ranking_exception.get("reason"), f"{path}.ranking_exception.reason"):
                    if not ranking_exception["reason"].strip():
                        self.error("empty_ranking_exception_reason", f"{path}.ranking_exception.reason", "must explain the user-confirmed order")
            exception = entry.get("availability_exception")
            if exception is not None and self.object_fields(exception, f"{path}.availability_exception", {"confirmed_at", "reason"}):
                self.datetime(exception.get("confirmed_at"), f"{path}.availability_exception.confirmed_at")
                self.string(exception.get("reason"), f"{path}.availability_exception.reason", nullable=True)
        elif source_type == "time_constraint":
            constraint_id = entry.get("time_constraint_id")
            self.string(constraint_id, f"{path}.time_constraint_id")
            if isinstance(constraint_id, str) and constraint_id not in self.time_constraints:
                self.error("missing_reference", f"{path}.time_constraint_id", "referenced time constraint does not exist")
        if isinstance(entry, dict):
            self.schedule_entries.append(entry)

    def validate_schedule_ranks(self) -> None:
        if not self.timezone:
            return
        groups: Dict[str, List[Tuple[int, str]]] = defaultdict(list)
        for index, entry in enumerate(self.schedule_entries):
            if entry.get("source_type") != "todo":
                continue
            starts = parse_datetime_value(entry.get("starts_at"))
            rank = entry.get("daily_rank")
            if starts and is_int(rank):
                day = starts.astimezone(self.timezone).date().isoformat()
                groups[day].append((rank, f"$.schedule.entries[{index}].daily_rank"))
        for day, ranked in groups.items():
            actual = sorted(rank for rank, _ in ranked)
            expected = list(range(1, len(ranked) + 1))
            if actual != expected:
                self.error("invalid_daily_rank", "$.schedule.entries", f"todo ranks for {day} must be unique and continuous from 1; got {actual}")
        self.validate_semantic_schedule_ranks()

    def validate_semantic_schedule_ranks(self) -> None:
        if not self.timezone:
            return
        actual_by_day: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for entry in self.schedule_entries:
            if entry.get("source_type") != "todo":
                continue
            starts = parse_datetime_value(entry.get("starts_at"))
            if starts and is_int(entry.get("daily_rank")) and isinstance(entry.get("id"), str):
                day = starts.astimezone(self.timezone).date().isoformat()
                actual_by_day[day].append(entry)
        expected_days = derive_daily_rankings(self.plan, self.calculated_at).get("days", {})
        for day, entries in actual_by_day.items():
            has_any_exception = any(entry.get("ranking_exception") is not None for entry in entries)
            signatures = {
                (exception.get("confirmed_at"), exception.get("reason"))
                for entry in entries
                if isinstance((exception := entry.get("ranking_exception")), dict)
                and isinstance(exception.get("confirmed_at"), str)
                and isinstance(exception.get("reason"), str)
                and exception.get("reason", "").strip()
            }
            if len(signatures) == 1 and all(isinstance(entry.get("ranking_exception"), dict) for entry in entries):
                continue
            if has_any_exception:
                self.error(
                    "invalid_ranking_exception_scope",
                    "$.schedule.entries",
                    f"ranking_exception for {day} must be identical on every todo entry of that date",
                )
            actual = [entry["id"] for entry in sorted(entries, key=lambda item: (item["daily_rank"], item["id"]))]
            expected = [item["entry_id"] for item in expected_days.get(day, [])]
            if actual != expected:
                self.error(
                    "semantic_daily_rank_mismatch",
                    "$.schedule.entries",
                    f"todo attention order for {day} must be {expected}; got {actual}. Use rank_plan.py or a date-wide ranking_exception confirmed by the user",
                )

    def validate_schedule_overlaps(self) -> None:
        timed: List[Tuple[datetime, datetime, str, str]] = []
        for index, entry in enumerate(self.schedule_entries):
            starts = parse_datetime_value(entry.get("starts_at"))
            ends = parse_datetime_value(entry.get("ends_at"))
            if starts and ends and ends > starts:
                timed.append((starts, ends, entry.get("source_type"), f"$.schedule.entries[{index}]"))
        timed.sort(key=lambda item: (item[0], item[1]))
        for index, current in enumerate(timed):
            for previous in timed[:index]:
                if previous[1] <= current[0]:
                    continue
                if current[1] <= previous[0]:
                    continue
                if previous[2] == current[2] == "time_constraint":
                    continue
                self.error("illegal_schedule_overlap", current[3], f"overlaps {previous[3]}")

    def window_bounds(self, window_dates: Tuple[date, date]) -> Tuple[datetime, datetime]:
        assert self.timezone is not None
        starts = datetime.combine(window_dates[0], time.min, self.timezone)
        ends = datetime.combine(window_dates[1] + timedelta(days=1), time.min, self.timezone)
        return starts, ends

    def expected_constraint_occurrences(
        self,
        constraint: Dict[str, Any],
        window_dates: Tuple[date, date],
    ) -> List[Tuple[datetime, datetime]]:
        if not self.timezone or constraint.get("status") != "active":
            return []
        anchor = parse_datetime_value(constraint.get("starts_at"))
        anchor_end = parse_datetime_value(constraint.get("ends_at"))
        if not anchor or not anchor_end or anchor_end <= anchor:
            return []
        window_start, window_end = self.window_bounds(window_dates)
        recurrence = constraint.get("recurrence")
        if recurrence is None:
            return [(anchor, anchor_end)] if anchor < window_end and anchor_end > window_start else []
        if not isinstance(recurrence, dict):
            return []
        interval = recurrence.get("interval")
        unit = recurrence.get("unit")
        if not is_int(interval) or interval <= 0 or unit not in {"day", "week", "month"}:
            return []
        exceptions: Dict[datetime, Dict[str, Any]] = {}
        for exception in recurrence.get("exceptions", []):
            if isinstance(exception, dict):
                key = parse_datetime_value(exception.get("occurrence_starts_at"))
                if key:
                    exceptions[key] = exception
        duration = anchor_end - anchor
        ends_on = parse_date_value(recurrence.get("ends_on")) if recurrence.get("ends_on") is not None else None
        results: List[Tuple[datetime, datetime]] = []
        processed: Set[datetime] = set()
        occurrence_index = 0
        while occurrence_index < 100000:
            occurrence_start = shift_local_datetime(anchor, unit, occurrence_index * interval, self.timezone)
            if ends_on and occurrence_start.astimezone(self.timezone).date() > ends_on:
                break
            occurrence_end = occurrence_start + duration
            if occurrence_start >= window_end and occurrence_end >= window_end:
                break
            exception = exceptions.get(occurrence_start)
            if exception:
                processed.add(occurrence_start)
                if exception.get("action") == "reschedule":
                    replacement_start = parse_datetime_value(exception.get("starts_at"))
                    replacement_end = parse_datetime_value(exception.get("ends_at"))
                    if replacement_start and replacement_end and replacement_start < window_end and replacement_end > window_start:
                        results.append((replacement_start, replacement_end))
            elif occurrence_start < window_end and occurrence_end > window_start:
                results.append((occurrence_start, occurrence_end))
            occurrence_index += 1
        if occurrence_index >= 100000:
            self.error("recurrence_expansion_limit", "$.time_constraints", "recurrence expansion exceeded safety limit")
        for key, exception in exceptions.items():
            if key in processed or exception.get("action") != "reschedule":
                continue
            replacement_start = parse_datetime_value(exception.get("starts_at"))
            replacement_end = parse_datetime_value(exception.get("ends_at"))
            if replacement_start and replacement_end and replacement_start < window_end and replacement_end > window_start:
                results.append((replacement_start, replacement_end))
        return results

    def validate_time_constraint_projections(self, window_dates: Optional[Tuple[date, date]]) -> None:
        if not window_dates or not self.timezone:
            return
        window_start, window_end = self.window_bounds(window_dates)
        expected: Counter[Tuple[str, datetime, datetime]] = Counter()
        for constraint_id, constraint in self.time_constraints.items():
            for starts, ends in self.expected_constraint_occurrences(constraint, window_dates):
                expected[(constraint_id, starts, ends)] += 1
        actual: Counter[Tuple[str, datetime, datetime]] = Counter()
        all_projection_keys: Counter[Tuple[str, datetime, datetime]] = Counter()
        for entry in self.schedule_entries:
            if entry.get("source_type") != "time_constraint":
                continue
            starts = parse_datetime_value(entry.get("starts_at"))
            ends = parse_datetime_value(entry.get("ends_at"))
            constraint_id = entry.get("time_constraint_id")
            if starts and ends and isinstance(constraint_id, str):
                projection_key = (constraint_id, starts, ends)
                all_projection_keys[projection_key] += 1
                constraint = self.time_constraints.get(constraint_id)
                if constraint and constraint.get("status") == "cancelled":
                    if self.calculated_at and ends > self.calculated_at:
                        self.error("cancelled_time_constraint_future_entry", "$.schedule.entries", f"cancelled time constraint {constraint_id} still occupies future time")
                elif constraint and constraint.get("status") == "active":
                    local_start = starts.astimezone(self.timezone).date()
                    week_start = local_start - timedelta(days=local_start.weekday())
                    week = (week_start, week_start + timedelta(days=6))
                    valid_keys = {
                        (constraint_id, expected_start, expected_end)
                        for expected_start, expected_end in self.expected_constraint_occurrences(constraint, week)
                    }
                    if projection_key not in valid_keys:
                        self.error("invalid_time_constraint_projection", "$.schedule.entries", f"projection for {constraint_id} does not match its authoritative occurrence")
                if starts < window_end and ends > window_start:
                    actual[projection_key] += 1
        for key, count in all_projection_keys.items():
            if count > 1:
                self.error("duplicate_time_constraint_projection", "$.schedule.entries", f"the same projection appears {count} times for {key[0]}")
        for key, count in (expected - actual).items():
            constraint_id, starts, ends = key
            self.error("missing_time_constraint_projection", "$.schedule.entries", f"missing {count} projection(s) for {constraint_id} at {starts.isoformat()}–{ends.isoformat()}")
        for key, count in (actual - expected).items():
            constraint_id, starts, ends = key
            self.error("invalid_time_constraint_projection", "$.schedule.entries", f"unexpected {count} projection(s) for {constraint_id} at {starts.isoformat()}–{ends.isoformat()}")

    def validate_todo_schedule_semantics(self, window_dates: Optional[Tuple[date, date]]) -> None:
        if not self.timezone or not self.calculated_at:
            return
        future_by_todo: Dict[str, List[Tuple[datetime, datetime, Dict[str, Any], str]]] = defaultdict(list)
        for index, entry in enumerate(self.schedule_entries):
            if entry.get("source_type") != "todo":
                continue
            starts = parse_datetime_value(entry.get("starts_at"))
            ends = parse_datetime_value(entry.get("ends_at"))
            todo_id = entry.get("todo_id")
            if not starts or not ends or not isinstance(todo_id, str):
                continue
            path = f"$.schedule.entries[{index}]"
            todo = self.todos.get(todo_id)
            if todo and starts > self.calculated_at:
                self.validate_todo_entry(todo, entry, starts, ends, path)
            if starts > self.calculated_at:
                future_by_todo[todo_id].append((starts, ends, entry, path))
            if todo and todo.get("status") in {"completed", "cancelled"} and ends > self.calculated_at:
                self.error("terminal_todo_future_entry", path, "completed or cancelled todo cannot occupy future time")
        for todo_id, entries in future_by_todo.items():
            todo = self.todos.get(todo_id)
            if not todo:
                continue
            entries.sort(key=lambda item: item[0])
            total = sum(duration_minutes(starts, ends) for starts, ends, _, _ in entries)
            remaining = todo.get("remaining_estimate_minutes")
            if not is_int(remaining):
                continue
            if todo.get("execution_mode") == "single_block" and entries:
                if len(entries) != 1 or duration_minutes(entries[0][0], entries[0][1]) != remaining:
                    self.error("invalid_single_block_coverage", entries[0][3], "single_block future coverage must be one continuous entry equal to remaining work")
            elif total > remaining:
                self.warning("future_coverage_exceeds_remaining", entries[0][3], f"future coverage is {total} minutes but remaining work is {remaining} minutes")
        self.validate_todo_dependencies(future_by_todo)
        self.validate_required_habit_spacing(future_by_todo)

    def validate_todo_entry(
        self,
        todo: Dict[str, Any],
        entry: Dict[str, Any],
        starts: datetime,
        ends: datetime,
        path: str,
    ) -> None:
        window = todo.get("execution_window")
        if isinstance(window, dict):
            window_start = parse_datetime_value(window.get("starts_at"))
            window_end = parse_datetime_value(window.get("ends_at"))
            if window_start and starts < window_start:
                self.error("todo_outside_execution_window", path, "starts before the todo execution_window")
            if window_end and ends > window_end:
                self.error("todo_outside_execution_window", path, "ends after the todo execution_window")
        deadlines: List[datetime] = []
        own_deadline = parse_datetime_value(todo.get("deadline"))
        if own_deadline:
            deadlines.append(own_deadline)
        for parent in todo.get("parent_refs", []):
            if not isinstance(parent, dict):
                continue
            goal = self.goals.get(parent.get("goal_id"))
            node = self.nodes.get(parent.get("node_id")) if parent.get("node_id") else None
            if goal and goal.get("status") == "active" and (node is None or node.get("status") not in {"completed", "cancelled"}):
                goal_deadline = parse_datetime_value(goal.get("deadline"))
                node_deadline = parse_datetime_value(node.get("deadline")) if node else None
                if goal_deadline:
                    deadlines.append(goal_deadline)
                if node_deadline:
                    deadlines.append(node_deadline)
        if deadlines and ends > min(deadlines):
            self.error("todo_after_deadline", path, "ends after an effective todo, node, or goal deadline")
        if entry.get("availability_exception") is None:
            segment = self.availability_segment_for_entry(starts, ends, path)
            if segment and todo.get("intensity") == "high" and segment.get("energy_level") == "low":
                self.warning("energy_mismatch", path, "high-intensity todo is placed in a low-energy segment")
        self.validate_habit_window(todo, starts, ends, path)

    def availability_segment_for_entry(
        self, starts: datetime, ends: datetime, path: str
    ) -> Optional[Dict[str, Any]]:
        rules = self.plan.get("availability_rules")
        if not isinstance(rules, dict):
            return None
        local_start = starts.astimezone(self.timezone)
        local_end = ends.astimezone(self.timezone)
        day = local_start.date()
        profile: Optional[Dict[str, Any]] = None
        for override in rules.get("date_overrides", []):
            if not isinstance(override, dict):
                continue
            override_start = parse_date_value(override.get("starts_on"))
            override_end = parse_date_value(override.get("ends_on"))
            if override_start and override_end and override_start <= day <= override_end:
                profile = override
                break
        if profile is None:
            classification = self.date_classifications.get(day.isoformat())
            if classification not in {"workday", "holiday"}:
                self.error("missing_date_classification", path, f"{day.isoformat()} requires a confirmed workday/holiday classification")
                return None
            profile = rules.get(classification)
        if not isinstance(profile, dict):
            return None
        start_time = local_start.timetz().replace(tzinfo=None)
        end_time = local_end.timetz().replace(tzinfo=None)
        for segment in profile.get("segments", []):
            if not isinstance(segment, dict):
                continue
            segment_start = parse_time_value(segment.get("starts_time"))
            segment_end = parse_time_value(segment.get("ends_time"))
            if segment_start and segment_end and segment_start <= start_time and end_time <= segment_end:
                return segment
        self.error("todo_outside_availability", path, "todo entry is outside the complete availability profile and has no exception")
        return None

    def habit_parents(self, todo: Dict[str, Any]) -> Iterable[Tuple[Dict[str, Any], Dict[str, Any]]]:
        for parent in todo.get("parent_refs", []):
            if not isinstance(parent, dict):
                continue
            goal = self.goals.get(parent.get("goal_id"))
            if goal and goal.get("type") == "habit":
                yield parent, goal

    def validate_habit_window(self, todo: Dict[str, Any], starts: datetime, ends: datetime, path: str) -> None:
        local_start = starts.astimezone(self.timezone)
        local_end = ends.astimezone(self.timezone)
        for _, goal in self.habit_parents(todo):
            rule = goal.get("habit_rule")
            if not isinstance(rule, dict):
                continue
            windows = rule.get("preferred_windows", [])
            matches_required = []
            matches_preferred = []
            for window in windows:
                if not isinstance(window, dict):
                    continue
                weekdays = window.get("weekdays")
                allowed_day = weekdays is None or local_start.isoweekday() in weekdays
                window_start = parse_time_value(window.get("starts_time"))
                window_end = parse_time_value(window.get("ends_time"))
                matches = bool(
                    allowed_day and window_start and window_end
                    and window_start <= local_start.timetz().replace(tzinfo=None)
                    and local_end.timetz().replace(tzinfo=None) <= window_end
                )
                (matches_required if window.get("strictness") == "required" else matches_preferred).append(matches)
            if matches_required and not any(matches_required):
                self.error("habit_required_window", path, "habit entry violates all required preferred_windows")
            if matches_preferred and not any(matches_preferred):
                self.warning("habit_preferred_window", path, "habit entry does not match a preferred window")

    def validate_todo_dependencies(
        self,
        future_by_todo: Dict[str, List[Tuple[datetime, datetime, Dict[str, Any], str]]],
    ) -> None:
        dependency_coverage: Dict[str, List[Tuple[datetime, datetime, Dict[str, Any], str]]] = defaultdict(list)
        for todo_id, entries in future_by_todo.items():
            dependency_coverage[todo_id].extend(entries)
        for index, entry in enumerate(self.schedule_entries):
            if entry.get("source_type") != "todo":
                continue
            starts = parse_datetime_value(entry.get("starts_at"))
            ends = parse_datetime_value(entry.get("ends_at"))
            todo_id = entry.get("todo_id")
            if (
                starts
                and ends
                and isinstance(todo_id, str)
                and starts <= self.calculated_at < ends
            ):
                dependency_coverage[todo_id].append(
                    (starts, ends, entry, f"$.schedule.entries[{index}]")
                )
        completion: Dict[str, Optional[datetime]] = {}
        for todo_id, todo in self.todos.items():
            if todo.get("status") == "completed":
                completion[todo_id] = self.calculated_at
                continue
            remaining = todo.get("remaining_estimate_minutes")
            if not is_int(remaining) or remaining <= 0:
                completion[todo_id] = None
                continue
            accumulated = 0
            completion_time: Optional[datetime] = None
            for starts, ends, _, _ in sorted(dependency_coverage.get(todo_id, []), key=lambda item: item[0]):
                accumulated += duration_minutes(starts, ends)
                if accumulated >= remaining:
                    completion_time = ends
                    break
            completion[todo_id] = completion_time
        for todo_id, entries in future_by_todo.items():
            todo = self.todos.get(todo_id)
            if not todo or not entries:
                continue
            first_start = min(entry[0] for entry in entries)
            first_path = min(entries, key=lambda item: item[0])[3]
            for dependency in todo.get("depends_on_todo_ids", []):
                predecessor = self.todos.get(dependency)
                if not predecessor:
                    continue
                if predecessor.get("status") == "completed":
                    continue
                expected_completion = completion.get(dependency)
                if expected_completion is None:
                    self.error("dependency_completion_unknown", first_path, f"predecessor {dependency} is not completed and lacks full future coverage")
                elif first_start < expected_completion:
                    self.error("dependency_order", first_path, f"starts before predecessor {dependency} is planned to complete")

    def validate_required_habit_spacing(
        self,
        future_by_todo: Dict[str, List[Tuple[datetime, datetime, Dict[str, Any], str]]],
    ) -> None:
        sessions: Dict[Tuple[str, str], List[Tuple[int, date, str, str]]] = defaultdict(list)
        for todo_id, entries in future_by_todo.items():
            todo = self.todos.get(todo_id)
            if not todo or not entries:
                continue
            first = min(entries, key=lambda item: item[0])
            session_date = first[0].astimezone(self.timezone).date()
            for parent, goal in self.habit_parents(todo):
                rule = goal.get("habit_rule")
                spacing = rule.get("spacing_rule") if isinstance(rule, dict) else None
                if not isinstance(spacing, dict):
                    continue
                period = parent.get("habit_period_starts_on")
                occurrence = parent.get("habit_occurrence_index")
                if isinstance(period, str) and is_int(occurrence):
                    sessions[(goal.get("id"), period)].append((occurrence, session_date, first[3], spacing.get("strictness")))
        for (goal_id, period), values in sessions.items():
            goal = self.goals.get(goal_id)
            rule = goal.get("habit_rule") if goal else None
            spacing = rule.get("spacing_rule") if isinstance(rule, dict) else None
            min_rest = spacing.get("min_rest_days") if isinstance(spacing, dict) else None
            if not is_int(min_rest):
                continue
            values.sort()
            for previous, current in zip(values, values[1:]):
                rest_days = (current[1] - previous[1]).days - 1
                if rest_days < min_rest:
                    level = "error" if current[3] == "required" else "warning"
                    self.issue(level, "habit_spacing", current[2], f"habit sessions in period {period} leave only {rest_days} full rest day(s); requires {min_rest}")


def load_json(path: str) -> Any:
    if path == "-":
        return json.load(sys.stdin)
    with Path(path).open("r", encoding="utf-8") as file:
        return json.load(file)


def parse_cli_datetime(value: str) -> datetime:
    parsed = parse_datetime_value(value)
    if parsed is None:
        raise argparse.ArgumentTypeError("must be an RFC 3339 datetime with a UTC offset")
    return parsed


def load_classifications(path: Optional[str]) -> Dict[str, str]:
    if path is None:
        return {}
    value = load_json(path)
    if not isinstance(value, dict):
        raise ValueError("date classifications must be a JSON object")
    result: Dict[str, str] = {}
    for key, classification in value.items():
        if parse_date_value(key) is None or classification not in {"workday", "holiday"}:
            raise ValueError("date classifications must map YYYY-MM-DD to workday or holiday")
        result[key] = classification
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate a complete schedule-assistant plan.")
    parser.add_argument("plan", help="candidate plan JSON path, or - for stdin")
    parser.add_argument("--calculated-at", type=parse_cli_datetime, help="RFC 3339 boundary for past, ongoing, and future entries")
    parser.add_argument("--date-classifications", help="JSON object mapping dates to workday or holiday")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        plan = load_json(args.plan)
        classifications = load_classifications(args.date_classifications)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        result = {
            "ok": False,
            "errors": [{"code": "input_error", "path": "$", "message": str(exc)}],
            "warnings": [],
            "summary": {"error_count": 1, "warning_count": 0},
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    result = Validator(plan, args.calculated_at, classifications).validate()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
