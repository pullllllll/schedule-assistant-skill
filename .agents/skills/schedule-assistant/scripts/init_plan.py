#!/usr/bin/env python3
"""Create a new, empty schedule-assistant plan without overwriting data."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def parse_timezone(value: str) -> ZoneInfo:
    """Return an IANA timezone or raise an argparse-friendly error."""
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise argparse.ArgumentTypeError(
            f"unknown IANA timezone: {value}"
        ) from exc


def parse_now(value: str) -> datetime:
    """Parse an RFC 3339 datetime that includes a UTC offset."""
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "--now must be an RFC 3339 datetime"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("--now must include a UTC offset")
    return parsed


def build_empty_plan(timezone_name: str, initialized_at: datetime) -> dict[str, Any]:
    """Build the exact empty structure defined by plan-data-contract.md."""
    timestamp = initialized_at.astimezone(ZoneInfo(timezone_name)).isoformat()
    return {
        "plan_meta": {
            "plan_version": 0,
            "timezone": timezone_name,
            "progress_reviewed_through": None,
            "updated_at": timestamp,
        },
        "task_structure": {"goals": [], "nodes": [], "todos": []},
        "time_constraints": [],
        "availability_rules": {
            "calendar_id": None,
            "workday": {
                "id": "availability-workday",
                "segments": [],
                "context": None,
                "created_at": timestamp,
                "updated_at": timestamp,
            },
            "holiday": {
                "id": "availability-holiday",
                "segments": [],
                "context": None,
                "created_at": timestamp,
                "updated_at": timestamp,
            },
            "date_overrides": [],
        },
        "schedule": {
            "planning_window": None,
            "generated_at": None,
            "entries": [],
        },
    }


def initialize_plan(path: Path, plan: dict[str, Any]) -> None:
    """Atomically create path and fail without replacing an existing plan."""
    parent = path.parent
    if not parent.is_dir():
        raise FileNotFoundError(f"parent directory does not exist: {parent}")

    payload = (json.dumps(plan, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(dir=parent, prefix=f".{path.name}.", delete=False) as file:
            temporary_path = Path(file.name)
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())

        # A hard link makes publication atomic and fails if the destination exists.
        os.link(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a legal empty plan.json without overwriting an existing file."
    )
    parser.add_argument("path", type=Path, help="destination plan.json path")
    parser.add_argument(
        "--timezone",
        required=True,
        type=parse_timezone,
        help="IANA timezone, for example Asia/Shanghai",
    )
    parser.add_argument(
        "--now",
        type=parse_now,
        help="optional RFC 3339 initialization time for deterministic runs",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    timezone_name = args.timezone.key
    initialized_at = args.now or datetime.now(args.timezone)
    plan = build_empty_plan(timezone_name, initialized_at)

    try:
        initialize_plan(args.path, plan)
    except FileExistsError:
        print(
            json.dumps(
                {"ok": False, "error": "plan_already_exists", "path": str(args.path)},
                ensure_ascii=False,
            ),
            file=os.sys.stderr,
        )
        return 2
    except OSError as exc:
        print(
            json.dumps(
                {"ok": False, "error": "write_failed", "message": str(exc)},
                ensure_ascii=False,
            ),
            file=os.sys.stderr,
        )
        return 1

    print(
        json.dumps(
            {"ok": True, "path": str(args.path), "plan_version": 0},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
