#!/usr/bin/env python3
"""Apply deterministic daily attention order to a complete candidate plan."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

from daily_ranking import derive_daily_rankings, parse_datetime


class RankingExceptionError(ValueError):
    pass


def _overridden_dates(plan: Dict[str, Any]) -> set[str]:
    timezone = ZoneInfo(plan["plan_meta"]["timezone"])
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for entry in plan.get("schedule", {}).get("entries", []):
        if entry.get("source_type") != "todo":
            continue
        starts = parse_datetime(entry.get("starts_at"))
        if starts:
            groups[starts.astimezone(timezone).date().isoformat()].append(entry)
    overridden: set[str] = set()
    for day, entries in groups.items():
        exceptions = [entry.get("ranking_exception") for entry in entries]
        if not any(exception is not None for exception in exceptions):
            continue
        signatures = {
            (exception.get("confirmed_at"), exception.get("reason"))
            for exception in exceptions
            if isinstance(exception, dict)
        }
        if (
            not all(isinstance(exception, dict) for exception in exceptions)
            or len(signatures) != 1
            or not all(isinstance(exception.get("reason"), str) and exception["reason"].strip() for exception in exceptions)
        ):
            raise RankingExceptionError(f"ranking_exception for {day} must be identical on every todo entry of that date")
        overridden.add(day)
    return overridden


def rank_plan(plan: Dict[str, Any], calculated_at: Any = None) -> Dict[str, Any]:
    ranked = copy.deepcopy(plan)
    original_entries = copy.deepcopy(ranked.get("schedule", {}).get("entries", []))
    for entry in ranked.get("schedule", {}).get("entries", []):
        if entry.get("source_type") == "todo":
            entry.setdefault("ranking_exception", None)
    overridden = _overridden_dates(ranked)
    derived = derive_daily_rankings(ranked, calculated_at)
    entry_by_id = {
        entry["id"]: entry
        for entry in ranked.get("schedule", {}).get("entries", [])
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }
    for day, assignments in derived["days"].items():
        if day in overridden:
            continue
        for assignment in assignments:
            entry = entry_by_id[assignment["entry_id"]]
            entry["daily_rank"] = assignment["daily_rank"]
            entry["priority_reason"] = assignment["priority_reason"]
    timestamp = parse_datetime(calculated_at)
    if original_entries != ranked.get("schedule", {}).get("entries", []) and timestamp is not None:
        ranked["schedule"]["generated_at"] = timestamp.isoformat()
    return ranked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--calculated-at")
    args = parser.parse_args()
    try:
        with args.candidate.open("r", encoding="utf-8") as file:
            plan = json.load(file)
        ranked = rank_plan(plan, args.calculated_at)
        args.out.write_text(json.dumps(ranked, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1
    changed = sum(
        1
        for old, new in zip(plan.get("schedule", {}).get("entries", []), ranked.get("schedule", {}).get("entries", []))
        if old != new
    )
    print(json.dumps({"ok": True, "out": str(args.out), "changed_entries": changed}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
