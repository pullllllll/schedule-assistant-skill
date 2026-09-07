#!/usr/bin/env python3
"""Safely validate and atomically commit a complete schedule-assistant plan."""

from __future__ import annotations

import argparse
import copy
import fcntl
import json
import os
import stat
import sys
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from validate_discovery import validate as validate_discovery
from validate_plan import Validator, load_classifications, parse_cli_datetime, parse_datetime_value


class ApplyFailure(Exception):
    def __init__(self, code: str, message: str, exit_code: int = 3) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code


def load_json_file(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def plan_version(plan: Any, label: str) -> int:
    if not isinstance(plan, dict):
        raise ApplyFailure("invalid_input", f"{label} must contain a JSON object")
    meta = plan.get("plan_meta")
    if not isinstance(meta, dict):
        raise ApplyFailure("invalid_input", f"{label}.plan_meta must be an object")
    version = meta.get("plan_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 0:
        raise ApplyFailure("invalid_input", f"{label}.plan_meta.plan_version must be a non-negative integer")
    return version


def plan_timezone(plan: Any) -> ZoneInfo:
    try:
        timezone_name = plan["plan_meta"]["timezone"]
        return ZoneInfo(timezone_name)
    except (KeyError, TypeError, ZoneInfoNotFoundError) as exc:
        raise ApplyFailure("invalid_input", "candidate plan has no valid IANA timezone") from exc


def collection_by_id(plan: Dict[str, Any], path: Tuple[str, ...]) -> Dict[str, Dict[str, Any]]:
    value: Any = plan
    for key in path:
        if not isinstance(value, dict):
            return {}
        value = value.get(key)
    if not isinstance(value, list):
        return {}
    result: Dict[str, Dict[str, Any]] = {}
    for item in value:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            result[item["id"]] = item
    return result


def unchanged_content(value: Dict[str, Any]) -> Dict[str, Any]:
    return {key: item for key, item in value.items() if key != "updated_at"}


def compare_persistent_objects(
    current: Dict[str, Any], candidate: Dict[str, Any]
) -> List[Dict[str, str]]:
    errors: List[Dict[str, str]] = []
    collections = [
        (("task_structure", "goals"), "goal"),
        (("task_structure", "nodes"), "node"),
        (("task_structure", "todos"), "todo"),
        (("time_constraints",), "time constraint"),
    ]
    for path, label in collections:
        old_items = collection_by_id(current, path)
        new_items = collection_by_id(candidate, path)
        for object_id, old_item in old_items.items():
            json_path = "$." + ".".join(path) + f"[{object_id}]"
            new_item = new_items.get(object_id)
            if new_item is None:
                errors.append(
                    {
                        "code": "persistent_object_removed",
                        "path": json_path,
                        "message": f"existing {label} must be retained and transitioned instead of being deleted",
                    }
                )
                continue
            if old_item.get("created_at") != new_item.get("created_at"):
                errors.append(
                    {
                        "code": "created_at_changed",
                        "path": f"{json_path}.created_at",
                        "message": "created_at of an existing object is immutable",
                    }
                )
            content_changed = unchanged_content(old_item) != unchanged_content(new_item)
            timestamp_changed = old_item.get("updated_at") != new_item.get("updated_at")
            if content_changed and not timestamp_changed:
                errors.append(
                    {
                        "code": "updated_at_not_advanced",
                        "path": f"{json_path}.updated_at",
                        "message": "updated_at must change when an existing object changes",
                    }
                )
            elif content_changed and timestamp_changed:
                old_updated = parse_datetime_value(old_item.get("updated_at"))
                new_updated = parse_datetime_value(new_item.get("updated_at"))
                if old_updated and new_updated and new_updated <= old_updated:
                    errors.append(
                        {
                            "code": "updated_at_not_advanced",
                            "path": f"{json_path}.updated_at",
                            "message": "updated_at must move forward when an existing object changes",
                        }
                    )
            elif not content_changed and timestamp_changed:
                errors.append(
                    {
                        "code": "spurious_updated_at_change",
                        "path": f"{json_path}.updated_at",
                        "message": "updated_at must not change when object content is unchanged",
                    }
                )

    current_rules = current.get("availability_rules")
    candidate_rules = candidate.get("availability_rules")
    if isinstance(current_rules, dict) and isinstance(candidate_rules, dict):
        for profile_name in ("workday", "holiday"):
            old_profile = current_rules.get(profile_name)
            new_profile = candidate_rules.get(profile_name)
            if isinstance(old_profile, dict) and isinstance(new_profile, dict):
                json_path = f"$.availability_rules.{profile_name}"
                if old_profile.get("id") != new_profile.get("id"):
                    errors.append(
                        {
                            "code": "stable_id_changed",
                            "path": f"{json_path}.id",
                            "message": "availability profile id is immutable",
                        }
                    )
                if old_profile.get("created_at") != new_profile.get("created_at"):
                    errors.append(
                        {
                            "code": "created_at_changed",
                            "path": f"{json_path}.created_at",
                            "message": "created_at of an existing profile is immutable",
                        }
                    )
                content_changed = unchanged_content(old_profile) != unchanged_content(new_profile)
                timestamp_changed = old_profile.get("updated_at") != new_profile.get("updated_at")
                if content_changed and not timestamp_changed:
                    errors.append(
                        {
                            "code": "updated_at_not_advanced",
                            "path": f"{json_path}.updated_at",
                            "message": "updated_at must change when the profile changes",
                        }
                    )
                elif content_changed and timestamp_changed:
                    old_updated = parse_datetime_value(old_profile.get("updated_at"))
                    new_updated = parse_datetime_value(new_profile.get("updated_at"))
                    if old_updated and new_updated and new_updated <= old_updated:
                        errors.append(
                            {
                                "code": "updated_at_not_advanced",
                                "path": f"{json_path}.updated_at",
                                "message": "updated_at must move forward when the profile changes",
                            }
                        )
                elif not content_changed and timestamp_changed:
                    errors.append(
                        {
                            "code": "spurious_updated_at_change",
                            "path": f"{json_path}.updated_at",
                            "message": "updated_at must not change when profile content is unchanged",
                        }
                    )

        old_overrides = collection_by_id(current, ("availability_rules", "date_overrides"))
        new_overrides = collection_by_id(candidate, ("availability_rules", "date_overrides"))
        for override_id, old_override in old_overrides.items():
            new_override = new_overrides.get(override_id)
            if new_override is None:
                continue
            json_path = f"$.availability_rules.date_overrides[{override_id}]"
            if old_override.get("created_at") != new_override.get("created_at"):
                errors.append(
                    {
                        "code": "created_at_changed",
                        "path": f"{json_path}.created_at",
                        "message": "created_at of an existing override is immutable",
                    }
                )
            content_changed = unchanged_content(old_override) != unchanged_content(new_override)
            old_updated = parse_datetime_value(old_override.get("updated_at"))
            new_updated = parse_datetime_value(new_override.get("updated_at"))
            if content_changed and old_override.get("updated_at") == new_override.get("updated_at"):
                errors.append(
                    {
                        "code": "updated_at_not_advanced",
                        "path": f"{json_path}.updated_at",
                        "message": "updated_at must change when the override changes",
                    }
                )
            elif content_changed and old_updated and new_updated and new_updated <= old_updated:
                errors.append(
                    {
                        "code": "updated_at_not_advanced",
                        "path": f"{json_path}.updated_at",
                        "message": "updated_at must move forward when the override changes",
                    }
                )
            elif not content_changed and old_override.get("updated_at") != new_override.get("updated_at"):
                errors.append(
                    {
                        "code": "spurious_updated_at_change",
                        "path": f"{json_path}.updated_at",
                        "message": "updated_at must not change when override content is unchanged",
                    }
                )
    return errors


def compare_progress_checkpoint(
    current: Dict[str, Any], candidate: Dict[str, Any]
) -> List[Dict[str, str]]:
    old_raw = current.get("plan_meta", {}).get("progress_reviewed_through")
    new_raw = candidate.get("plan_meta", {}).get("progress_reviewed_through")
    old_value = parse_datetime_value(old_raw) if old_raw is not None else None
    new_value = parse_datetime_value(new_raw) if new_raw is not None else None
    if old_value is not None and (new_value is None or new_value < old_value):
        return [
            {
                "code": "progress_checkpoint_rollback",
                "path": "$.plan_meta.progress_reviewed_through",
                "message": "progress_reviewed_through cannot move backward or return to null",
            }
        ]
    return []


def comparable_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    value = copy.deepcopy(plan)
    meta = value.get("plan_meta")
    if isinstance(meta, dict):
        meta.pop("plan_version", None)
        meta.pop("updated_at", None)
    return value


def has_task_structure(plan: Dict[str, Any]) -> bool:
    structure = plan.get("task_structure")
    if not isinstance(structure, dict):
        return False
    return any(isinstance(structure.get(key), list) and structure[key] for key in ("goals", "nodes", "todos"))


def has_planning_window(plan: Dict[str, Any]) -> bool:
    schedule = plan.get("schedule")
    return isinstance(schedule, dict) and schedule.get("planning_window") is not None


def requires_first_discovery(current: Dict[str, Any], candidate: Dict[str, Any]) -> bool:
    return (
        (not has_task_structure(current) and has_task_structure(candidate))
        or (not has_planning_window(current) and has_planning_window(candidate))
    )


def has_unowned_dependency_chain(plan: Dict[str, Any]) -> bool:
    todos = collection_by_id(plan, ("task_structure", "todos"))
    for todo_item in todos.values():
        dependencies = todo_item.get("depends_on_todo_ids")
        if todo_item.get("parent_refs") or not isinstance(dependencies, list):
            continue
        for dependency_id in dependencies:
            predecessor = todos.get(dependency_id)
            if predecessor is not None and not predecessor.get("parent_refs"):
                return True
    return False


def requires_repair_discovery(current: Dict[str, Any], candidate: Dict[str, Any]) -> bool:
    affected_content_changed = (
        current.get("task_structure") != candidate.get("task_structure")
        or current.get("schedule") != candidate.get("schedule")
    )
    return affected_content_changed and has_unowned_dependency_chain(current)


def requires_incremental_discovery(current: Dict[str, Any], candidate: Dict[str, Any]) -> bool:
    current_goals = collection_by_id(current, ("task_structure", "goals"))
    candidate_goals = collection_by_id(candidate, ("task_structure", "goals"))
    if set(candidate_goals) - set(current_goals):
        return True

    current_nodes = collection_by_id(current, ("task_structure", "nodes"))
    candidate_nodes = collection_by_id(candidate, ("task_structure", "nodes"))
    if set(candidate_nodes) - set(current_nodes):
        return True
    for node_id, current_node in current_nodes.items():
        candidate_node = candidate_nodes.get(node_id)
        if candidate_node is None:
            continue
        for field in ("goal_id", "depends_on_node_ids"):
            if current_node.get(field) != candidate_node.get(field):
                return True

    current_todos = collection_by_id(current, ("task_structure", "todos"))
    candidate_todos = collection_by_id(candidate, ("task_structure", "todos"))
    for todo_id, candidate_todo in candidate_todos.items():
        current_todo = current_todos.get(todo_id)
        if current_todo is None:
            if candidate_todo.get("parent_refs") or candidate_todo.get("depends_on_todo_ids"):
                return True
            continue
        for field in ("parent_refs", "depends_on_todo_ids"):
            if current_todo.get(field) != candidate_todo.get(field):
                return True
    return False


def cross_version_errors(
    current: Dict[str, Any], candidate: Dict[str, Any]
) -> List[Dict[str, str]]:
    errors = compare_progress_checkpoint(current, candidate)
    errors.extend(compare_persistent_objects(current, candidate))
    old_schedule = current.get("schedule")
    new_schedule = candidate.get("schedule")
    if isinstance(old_schedule, dict) and isinstance(new_schedule, dict):
        old_content = {key: value for key, value in old_schedule.items() if key != "generated_at"}
        new_content = {key: value for key, value in new_schedule.items() if key != "generated_at"}
        if old_content != new_content:
            old_generated = parse_datetime_value(old_schedule.get("generated_at"))
            new_generated = parse_datetime_value(new_schedule.get("generated_at"))
            if new_generated is None or (old_generated is not None and new_generated <= old_generated):
                errors.append(
                    {
                        "code": "generated_at_not_advanced",
                        "path": "$.schedule.generated_at",
                        "message": "generated_at must move forward when the schedule content changes",
                    }
                )
    if comparable_plan(current) == comparable_plan(candidate):
        errors.append(
            {
                "code": "no_changes",
                "path": "$",
                "message": "candidate does not contain a formal plan change",
            }
        )
    return errors


def encode_plan(plan: Dict[str, Any]) -> bytes:
    return (json.dumps(plan, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def atomic_replace(path: Path, payload: bytes, original_mode: int) -> Optional[str]:
    temporary_path: Optional[Path] = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as file:
            temporary_path = Path(file.name)
            os.chmod(file.name, stat.S_IMODE(original_mode))
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            return f"plan was committed atomically, but directory durability sync failed: {exc}"
        return None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def failure_result(code: str, message: str, errors: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    return {
        "ok": False,
        "error": code,
        "message": message,
        "errors": errors or [],
        "warnings": [],
    }


def apply_plan(
    candidate_path: Path,
    official_path: Path,
    expected_version: int,
    committed_at: Optional[datetime],
    classifications: Dict[str, str],
    discovery_state_path: Optional[Path],
) -> Tuple[int, Dict[str, Any]]:
    try:
        if candidate_path.resolve() == official_path.resolve():
            raise ApplyFailure("same_input_and_output", "candidate and official plan must be different files")
        candidate = load_json_file(candidate_path)
        candidate_version = plan_version(candidate, "candidate")
        if candidate_version != expected_version:
            raise ApplyFailure(
                "candidate_version_mismatch",
                f"candidate version {candidate_version} does not match expected base version {expected_version}",
                2,
            )
        if not official_path.is_file():
            raise ApplyFailure("official_plan_missing", f"official plan does not exist: {official_path}")

        lock_path = official_path.with_name(f".{official_path.name}.lock")
        with lock_path.open("a+b") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ApplyFailure("plan_busy", "another plan commit is already in progress") from exc

            current = load_json_file(official_path)
            current_version = plan_version(current, "official plan")
            if current_version != expected_version:
                raise ApplyFailure(
                    "version_conflict",
                    f"official plan is version {current_version}, expected {expected_version}; regenerate the candidate from the latest plan",
                    2,
                )

            discovery_state = None
            required_discovery_mode = None
            missing_discovery_error = None
            if requires_first_discovery(current, candidate):
                required_discovery_mode = "first_discovery"
                missing_discovery_error = "discovery_readiness_required"
            elif requires_repair_discovery(current, candidate):
                required_discovery_mode = "repair_discovery"
                missing_discovery_error = "repair_discovery_required"
            elif requires_incremental_discovery(current, candidate):
                required_discovery_mode = "incremental_discovery"
                missing_discovery_error = "incremental_discovery_required"
            if discovery_state_path is not None:
                discovery_state = load_json_file(discovery_state_path)
                discovery_validation = validate_discovery(discovery_state)
                scheduling_gate = isinstance(discovery_state, dict) and discovery_state.get("gate") == "scheduling"
                expected_mode = (
                    required_discovery_mode is None
                    or (isinstance(discovery_state, dict) and discovery_state.get("mode") == required_discovery_mode)
                )
                if not expected_mode:
                    discovery_validation["errors"].append(
                        {
                            "code": "wrong_discovery_mode",
                            "path": "$.mode",
                            "message": f"this commit requires {required_discovery_mode}",
                        }
                    )
                    discovery_validation["ok"] = False
                if not scheduling_gate or not discovery_validation["ok"]:
                    return 1, failure_result(
                        "discovery_readiness_failed",
                        "candidate was not committed because discovery is not ready for scheduling",
                        discovery_validation["errors"],
                    )
            if required_discovery_mode is not None and discovery_state is None:
                return 1, failure_result(
                    missing_discovery_error or "discovery_readiness_required",
                    f"this commit requires a validated {required_discovery_mode} scheduling state",
                )

            timezone = plan_timezone(candidate)
            commit_time = committed_at.astimezone(timezone) if committed_at else datetime.now(timezone)
            current_updated = parse_datetime_value(current.get("plan_meta", {}).get("updated_at"))
            if current_updated and commit_time < current_updated:
                raise ApplyFailure("commit_time_rollback", "commit time cannot be earlier than the official plan updated_at")

            version_errors = cross_version_errors(current, candidate)
            if version_errors:
                return 1, failure_result("cross_version_validation_failed", "candidate violates cross-version invariants", version_errors)

            committed_plan = copy.deepcopy(candidate)
            committed_plan["plan_meta"]["plan_version"] = expected_version + 1
            committed_plan["plan_meta"]["updated_at"] = commit_time.isoformat()
            checkpoint = parse_datetime_value(committed_plan["plan_meta"].get("progress_reviewed_through"))
            if checkpoint and checkpoint > commit_time:
                error = {
                    "code": "progress_checkpoint_in_future",
                    "path": "$.plan_meta.progress_reviewed_through",
                    "message": "progress_reviewed_through cannot be later than the commit time",
                }
                return 1, failure_result("cross_version_validation_failed", "candidate violates cross-version invariants", [error])

            validation = Validator(committed_plan, commit_time, classifications).validate()
            if not validation["ok"]:
                return 1, {
                    "ok": False,
                    "error": "validation_failed",
                    "message": "candidate was not committed",
                    "errors": validation["errors"],
                    "warnings": validation["warnings"],
                }

            durability_warning = atomic_replace(
                official_path,
                encode_plan(committed_plan),
                official_path.stat().st_mode,
            )
            warnings = list(validation["warnings"])
            if durability_warning:
                warnings.append(
                    {
                        "code": "directory_sync_warning",
                        "path": str(official_path),
                        "message": durability_warning,
                    }
                )
            return 0, {
                "ok": True,
                "path": str(official_path),
                "previous_version": expected_version,
                "plan_version": expected_version + 1,
                "updated_at": commit_time.isoformat(),
                "warnings": warnings,
            }
    except ApplyFailure as exc:
        return exc.exit_code, failure_result(exc.code, exc.message)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return 3, failure_result("apply_failed", f"candidate was not committed: {exc}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate and atomically apply a complete candidate plan.")
    parser.add_argument("candidate", type=Path, help="complete candidate plan JSON")
    parser.add_argument("official", type=Path, help="existing official plan.json")
    parser.add_argument("--expected-version", required=True, type=int, help="base plan_version used to build the candidate")
    parser.add_argument("--committed-at", type=parse_cli_datetime, help="optional deterministic RFC 3339 commit time")
    parser.add_argument("--date-classifications", help="JSON object mapping dates to workday or holiday")
    parser.add_argument(
        "--discovery-state",
        type=Path,
        help="transient discovery state; mandatory for first discovery, repair discovery, and incremental structural changes",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.expected_version < 0:
        result = failure_result("invalid_expected_version", "--expected-version must be non-negative")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 3
    try:
        classifications = load_classifications(args.date_classifications)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        result = failure_result("invalid_date_classifications", str(exc))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 3
    exit_code, result = apply_plan(
        args.candidate,
        args.official,
        args.expected_version,
        args.committed_at,
        classifications,
        args.discovery_state,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
