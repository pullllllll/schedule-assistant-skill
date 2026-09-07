from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1]
VALIDATOR = SCRIPT_DIR / "validate_discovery.py"


def branch(
    branch_id: str,
    *,
    status: str = "ready",
    outcome_check: str = "complete",
    domain_scope_check: str = "complete",
    relationship_check: str = "complete",
    object_classification_check: str = "complete",
    unresolved_questions: list[str] | None = None,
    structure_source: str = "none",
    confirmation: str = "not_required",
    deferred_reason: str | None = None,
) -> dict:
    return {
        "id": branch_id,
        "title": branch_id,
        "status": status,
        "outcome_check": outcome_check,
        "domain_scope_check": domain_scope_check,
        "relationship_check": relationship_check,
        "object_classification_check": object_classification_check,
        "unresolved_questions": unresolved_questions or [],
        "structure_source": structure_source,
        "confirmation": confirmation,
        "deferred_reason": deferred_reason,
    }


def valid_state() -> dict:
    return {
        "gate": "scheduling",
        "mode": "first_discovery",
        "breadth_scan_complete": True,
        "structural_audit_complete": True,
        "branches": [
            branch("work", structure_source="agent_derived", confirmation="confirmed"),
            branch("cycling"),
            branch("social"),
        ],
    }


class ValidateDiscoveryTests(unittest.TestCase):
    def validate(self, state: dict) -> tuple[int, dict]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "discovery.json"
            path.write_text(json.dumps(state), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(VALIDATOR), str(path)],
                check=False,
                capture_output=True,
                text=True,
            )
            payload = json.loads(result.stdout) if result.stdout else {}
            return result.returncode, payload

    def codes(self, result: dict) -> set[str]:
        return {item["code"] for item in result.get("errors", [])}

    def test_ready_multibranch_discovery_passes(self) -> None:
        code, result = self.validate(valid_state())
        self.assertEqual(code, 0, result)
        self.assertTrue(result["ok"])

    def test_open_branch_blocks_readiness(self) -> None:
        state = valid_state()
        state["branches"][1] = branch(
            "cycling", status="open", relationship_check="pending"
        )
        code, result = self.validate(state)
        self.assertEqual(code, 1, result)
        self.assertIn("open_branch", self.codes(result))
        self.assertIn("pending_relationship_check", self.codes(result))

    def test_missing_breadth_scan_blocks_readiness(self) -> None:
        state = valid_state()
        state["breadth_scan_complete"] = False
        code, result = self.validate(state)
        self.assertEqual(code, 1, result)
        self.assertIn("breadth_scan_incomplete", self.codes(result))

    def test_ready_branch_requires_goal_outcome_check(self) -> None:
        state = valid_state()
        state["branches"][0]["outcome_check"] = "pending"
        code, result = self.validate(state)
        self.assertEqual(code, 1, result)
        self.assertIn("pending_outcome_check", self.codes(result))

    def test_ready_branch_requires_domain_scope_check(self) -> None:
        state = valid_state()
        state["branches"][0]["domain_scope_check"] = "pending"
        code, result = self.validate(state)
        self.assertEqual(code, 1, result)
        self.assertIn("pending_domain_scope_check", self.codes(result))

    def test_ready_branch_requires_object_classification_check(self) -> None:
        state = valid_state()
        state["branches"][0]["object_classification_check"] = "pending"
        code, result = self.validate(state)
        self.assertEqual(code, 1, result)
        self.assertIn("pending_object_classification_check", self.codes(result))

    def test_unresolved_questions_block_readiness(self) -> None:
        state = valid_state()
        state["branches"][0]["unresolved_questions"] = [
            "安装是用户自己完成还是师傅上门"
        ]
        code, result = self.validate(state)
        self.assertEqual(code, 1, result)
        self.assertIn("unresolved_questions", self.codes(result))

    def test_agent_derived_structure_requires_confirmation(self) -> None:
        state = valid_state()
        state["branches"][0]["confirmation"] = "pending"
        code, result = self.validate(state)
        self.assertEqual(code, 1, result)
        self.assertIn("structure_confirmation_pending", self.codes(result))

    def test_proposal_gate_allows_pending_agent_confirmation(self) -> None:
        state = valid_state()
        state["gate"] = "proposal"
        state["branches"][0]["confirmation"] = "pending"
        code, result = self.validate(state)
        self.assertEqual(code, 0, result)
        self.assertTrue(result["ok"])

    def test_proposal_gate_rejects_premature_agent_confirmation(self) -> None:
        state = valid_state()
        state["gate"] = "proposal"
        code, result = self.validate(state)
        self.assertEqual(code, 1, result)
        self.assertIn("invalid_confirmation_state", self.codes(result))

    def test_user_explicit_structure_does_not_require_confirmation(self) -> None:
        state = valid_state()
        state["branches"][0]["structure_source"] = "user_explicit"
        state["branches"][0]["confirmation"] = "not_required"
        code, result = self.validate(state)
        self.assertEqual(code, 0, result)
        self.assertTrue(result["ok"])

    def test_user_explicit_structure_rejects_confirmed_state(self) -> None:
        state = valid_state()
        state["branches"][0]["structure_source"] = "user_explicit"
        code, result = self.validate(state)
        self.assertEqual(code, 1, result)
        self.assertIn("invalid_confirmation_state", self.codes(result))

    def test_deferred_branch_requires_reason(self) -> None:
        state = valid_state()
        state["branches"][1] = branch(
            "cycling", status="deferred", deferred_reason=None
        )
        code, result = self.validate(state)
        self.assertEqual(code, 1, result)
        self.assertIn("missing_deferred_reason", self.codes(result))

    def test_repair_mode_requires_structural_audit(self) -> None:
        state = valid_state()
        state["mode"] = "repair_discovery"
        state["structural_audit_complete"] = False
        code, result = self.validate(state)
        self.assertEqual(code, 1, result)
        self.assertIn("structural_audit_incomplete", self.codes(result))


if __name__ == "__main__":
    unittest.main()
