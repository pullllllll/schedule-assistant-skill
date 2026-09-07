#!/usr/bin/env python3
"""Emit workday/holiday classifications for a date range.

validate_plan.py and apply_plan.py require a confirmed classification for every
date that carries a todo entry. This helper produces the ordinary
weekday/weekend baseline so the caller only has to state real exceptions
(makeup workdays, public holidays), which the model must never invent.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not an ISO date: {value}") from exc


def parse_overrides(value: str | None) -> Dict[str, str]:
    """Accept `2026-10-11=workday,2026-10-01=holiday`."""
    if not value:
        return {}
    result: Dict[str, str] = {}
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise argparse.ArgumentTypeError(f"expected DATE=workday|holiday, got {item}")
        day, classification = item.split("=", 1)
        parse_date(day.strip())
        if classification.strip() not in {"workday", "holiday"}:
            raise argparse.ArgumentTypeError(f"classification must be workday or holiday: {item}")
        result[day.strip()] = classification.strip()
    return result


def build(start: date, end: date, overrides: Dict[str, str]) -> Dict[str, str]:
    if end < start:
        raise SystemExit(json.dumps({"ok": False, "error": "range_order"}, ensure_ascii=False))
    result: Dict[str, str] = {}
    day = start
    while day <= end:
        key = day.isoformat()
        result[key] = overrides.get(key, "holiday" if day.weekday() >= 5 else "workday")
        day += timedelta(days=1)
    unknown = sorted(set(overrides) - set(result))
    if unknown:
        raise SystemExit(json.dumps(
            {"ok": False, "error": "override_outside_range", "dates": unknown}, ensure_ascii=False))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("start", type=parse_date, help="first date, inclusive (YYYY-MM-DD)")
    parser.add_argument("end", type=parse_date, help="last date, inclusive (YYYY-MM-DD)")
    parser.add_argument("--override", type=parse_overrides, default={},
                        help="comma separated DATE=workday|holiday for confirmed exceptions")
    parser.add_argument("--out", type=Path, help="write JSON here instead of stdout")
    arguments = parser.parse_args()

    classifications = build(arguments.start, arguments.end, arguments.override)
    payload: Any = classifications
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if arguments.out:
        arguments.out.parent.mkdir(parents=True, exist_ok=True)
        arguments.out.write_text(text, encoding="utf-8")
        assumed = sorted(set(classifications) - set(arguments.override))
        print(json.dumps({
            "ok": True, "path": str(arguments.out), "days": len(classifications),
            "assumed_from_weekday": len(assumed), "confirmed_overrides": sorted(arguments.override),
        }, ensure_ascii=False))
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
