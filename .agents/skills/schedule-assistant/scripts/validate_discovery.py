#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List


MODES = {"first_discovery", "incremental_discovery", "repair_discovery"}
GATES = {"proposal", "scheduling"}
STATUSES = {"open", "ready", "deferred"}
SEMANTIC_CHECKS = {"pending", "complete", "not_applicable"}
RELATIONSHIP_CHECKS = {"pending", "complete"}
OBJECT_CLASSIFICATION_CHECKS = {"pending", "complete", "not_applicable"}
STRUCTURE_SOURCES = {"none", "user_explicit", "agent_derived"}
CONFIRMATIONS = {"not_required", "pending", "confirmed"}


def error(code: str, path: str, message: str) -> Dict[str, str]:
    return {"code": code, "path": path, "message": message}


def validate(state: Any) -> Dict[str, Any]:
    errors: List[Dict[str, str]] = []
    if not isinstance(state, dict):
        errors.append(error("invalid_root", "$", "discovery state must be an object"))
        return result(errors, 0)

    expected_fields = {
        "gate",
        "mode",
        "breadth_scan_complete",
        "structural_audit_complete",
        "branches",
    }
    extra_fields = sorted(set(state) - expected_fields)
    missing_fields = sorted(expected_fields - set(state))
    for field in missing_fields:
        errors.append(error("missing_field", f"$.{field}", "required field is missing"))
    for field in extra_fields:
        errors.append(error("unexpected_field", f"$.{field}", "field is not part of the discovery contract"))

    gate = state.get("gate")
    if gate not in GATES:
        errors.append(error("invalid_gate", "$.gate", f"must be one of {sorted(GATES)}"))

    mode = state.get("mode")
    if mode not in MODES:
        errors.append(error("invalid_mode", "$.mode", f"must be one of {sorted(MODES)}"))

    breadth_complete = state.get("breadth_scan_complete")
    if not isinstance(breadth_complete, bool):
        errors.append(error("invalid_boolean", "$.breadth_scan_complete", "must be boolean"))
    elif not breadth_complete:
        errors.append(
            error(
                "breadth_scan_incomplete",
                "$.breadth_scan_complete",
                "all in-scope discovery directions must be scanned before structure confirmation or scheduling",
            )
        )

    structural_audit_complete = state.get("structural_audit_complete")
    if not isinstance(structural_audit_complete, bool):
        errors.append(error("invalid_boolean", "$.structural_audit_complete", "must be boolean"))
    elif mode == "repair_discovery" and not structural_audit_complete:
        errors.append(
            error(
                "structural_audit_incomplete",
                "$.structural_audit_complete",
                "repair discovery must audit the affected existing structure before scheduling",
            )
        )

    branches = state.get("branches")
    if not isinstance(branches, list) or not branches:
        errors.append(error("missing_branches", "$.branches", "at least one discovery branch is required"))
        branches = []

    seen_ids = set()
    branch_fields = {
        "id",
        "title",
        "status",
        "outcome_check",
        "domain_scope_check",
        "relationship_check",
        "object_classification_check",
        "unresolved_questions",
        "structure_source",
        "confirmation",
        "deferred_reason",
    }
    for index, branch in enumerate(branches):
        path = f"$.branches[{index}]"
        if not isinstance(branch, dict):
            errors.append(error("invalid_branch", path, "branch must be an object"))
            continue
        for field in sorted(branch_fields - set(branch)):
            errors.append(error("missing_field", f"{path}.{field}", "required field is missing"))
        for field in sorted(set(branch) - branch_fields):
            errors.append(error("unexpected_field", f"{path}.{field}", "field is not part of the branch contract"))

        branch_id = branch.get("id")
        if not isinstance(branch_id, str) or not branch_id.strip():
            errors.append(error("invalid_branch_id", f"{path}.id", "must be a non-empty string"))
        elif branch_id in seen_ids:
            errors.append(error("duplicate_branch_id", f"{path}.id", "branch id must be unique"))
        else:
            seen_ids.add(branch_id)

        title = branch.get("title")
        if not isinstance(title, str) or not title.strip():
            errors.append(error("invalid_title", f"{path}.title", "must be a non-empty string"))

        status = branch.get("status")
        if status not in STATUSES:
            errors.append(error("invalid_status", f"{path}.status", f"must be one of {sorted(STATUSES)}"))
        elif status == "open":
            errors.append(
                error(
                    "open_branch",
                    f"{path}.status",
                    "an unresolved in-scope branch blocks structure confirmation and scheduling",
                )
            )

        outcome_check = branch.get("outcome_check")
        if outcome_check not in SEMANTIC_CHECKS:
            errors.append(
                error(
                    "invalid_outcome_check",
                    f"{path}.outcome_check",
                    f"must be one of {sorted(SEMANTIC_CHECKS)}",
                )
            )
        elif status == "ready" and outcome_check == "pending":
            errors.append(
                error(
                    "pending_outcome_check",
                    f"{path}.outcome_check",
                    "a ready branch must identify its intended result or explicitly establish that no Goal outcome applies",
                )
            )

        domain_scope_check = branch.get("domain_scope_check")
        if domain_scope_check not in SEMANTIC_CHECKS:
            errors.append(
                error(
                    "invalid_domain_scope_check",
                    f"{path}.domain_scope_check",
                    f"must be one of {sorted(SEMANTIC_CHECKS)}",
                )
            )
        elif status == "ready" and domain_scope_check == "pending":
            errors.append(
                error(
                    "pending_domain_scope_check",
                    f"{path}.domain_scope_check",
                    "a ready branch must check for adjacent responsibilities or actions in the same planning horizon",
                )
            )

        relationship_check = branch.get("relationship_check")
        if relationship_check not in RELATIONSHIP_CHECKS:
            errors.append(
                error(
                    "invalid_relationship_check",
                    f"{path}.relationship_check",
                    f"must be one of {sorted(RELATIONSHIP_CHECKS)}",
                )
            )
        elif relationship_check == "pending":
            errors.append(
                error(
                    "pending_relationship_check",
                    f"{path}.relationship_check",
                    "branch relationships and possible parent goals must be checked before scheduling",
                )
            )

        object_classification_check = branch.get("object_classification_check")
        if object_classification_check not in OBJECT_CLASSIFICATION_CHECKS:
            errors.append(
                error(
                    "invalid_object_classification_check",
                    f"{path}.object_classification_check",
                    f"must be one of {sorted(OBJECT_CLASSIFICATION_CHECKS)}",
                )
            )
        elif status == "ready" and object_classification_check == "pending":
            errors.append(
                error(
                    "pending_object_classification_check",
                    f"{path}.object_classification_check",
                    "a ready branch must classify the matter or explicitly establish that no formal object applies",
                )
            )

        unresolved_questions = branch.get("unresolved_questions")
        if not isinstance(unresolved_questions, list):
            errors.append(
                error(
                    "invalid_unresolved_questions",
                    f"{path}.unresolved_questions",
                    "must be an array of non-empty strings",
                )
            )
        else:
            for question_index, question in enumerate(unresolved_questions):
                if not isinstance(question, str) or not question.strip():
                    errors.append(
                        error(
                            "invalid_unresolved_question",
                            f"{path}.unresolved_questions[{question_index}]",
                            "must be a non-empty string",
                        )
                    )
            if unresolved_questions:
                errors.append(
                    error(
                        "unresolved_questions",
                        f"{path}.unresolved_questions",
                        "questions that can change structure or scheduling must be resolved before proposal or scheduling",
                    )
                )

        structure_source = branch.get("structure_source")
        if structure_source not in STRUCTURE_SOURCES:
            errors.append(
                error(
                    "invalid_structure_source",
                    f"{path}.structure_source",
                    f"must be one of {sorted(STRUCTURE_SOURCES)}",
                )
            )

        confirmation = branch.get("confirmation")
        if confirmation not in CONFIRMATIONS:
            errors.append(
                error(
                    "invalid_confirmation",
                    f"{path}.confirmation",
                    f"must be one of {sorted(CONFIRMATIONS)}",
                )
            )
        elif structure_source == "agent_derived":
            if gate == "proposal" and confirmation != "pending":
                errors.append(
                    error(
                        "invalid_confirmation_state",
                        f"{path}.confirmation",
                        "agent-derived structure must be pending at the proposal gate",
                    )
                )
            elif gate == "scheduling" and confirmation != "confirmed":
                errors.append(
                    error(
                        "structure_confirmation_pending",
                        f"{path}.confirmation",
                        "agent-derived goals, ownership, dependencies, or task splits require confirmation",
                    )
                )
        elif structure_source in {"none", "user_explicit"} and confirmation != "not_required":
            errors.append(
                error(
                    "invalid_confirmation_state",
                    f"{path}.confirmation",
                    "non-derived structure must use not_required confirmation",
                )
            )

        deferred_reason = branch.get("deferred_reason")
        if status == "deferred":
            if not isinstance(deferred_reason, str) or not deferred_reason.strip():
                errors.append(
                    error(
                        "missing_deferred_reason",
                        f"{path}.deferred_reason",
                        "a deferred branch must explain why it does not affect the current planning window",
                    )
                )
        elif deferred_reason is not None:
            errors.append(
                error(
                    "unexpected_deferred_reason",
                    f"{path}.deferred_reason",
                    "only deferred branches may have a deferred reason",
                )
            )

    return result(errors, len(branches))


def result(errors: List[Dict[str, str]], branch_count: int) -> Dict[str, Any]:
    return {
        "ok": not errors,
        "errors": errors,
        "summary": {"error_count": len(errors), "branch_count": branch_count},
    }


def load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        payload = result([error("invalid_input", "$", str(exc))], 0)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        raise SystemExit(2) from exc


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate transient multi-branch discovery readiness before structure confirmation or scheduling."
    )
    parser.add_argument("state", type=Path, help="transient discovery state JSON")
    args = parser.parse_args()
    payload = validate(load(args.state))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
