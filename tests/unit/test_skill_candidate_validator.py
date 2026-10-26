import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BUNDLE_ROOT = (
    REPOSITORY_ROOT
    / "stock-analyzer-skills"
    / "skills"
    / "stock-analyzer-quant-architect"
)
VALIDATOR_PATH = BUNDLE_ROOT / "scripts" / "validate_skill_candidate.py"
SPEC = importlib.util.spec_from_file_location(
    "jarvis_skill_candidate_validator",
    VALIDATOR_PATH,
)
assert SPEC is not None and SPEC.loader is not None
validator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validator
SPEC.loader.exec_module(validator)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _candidate() -> dict:
    target_path = Path("subskills/technical-analysis-engineer/SKILL.md")
    active_path = BUNDLE_ROOT / target_path
    fingerprint = "a" * 64
    return {
        "schema_version": 1,
        "candidate_id": "skill-change-technical-cpr-001",
        "status": "DRAFT",
        "created_at": "2026-09-06T12:00:00+05:30",
        "created_by": "maintainer",
        "target": {
            "bundle": "stock-analyzer-quant-architect",
            "skill_name": "technical-analysis-engineer",
            "skill_path": target_path.as_posix(),
            "domain": "TECHNICAL_ANALYSIS",
            "base_version": "1.0.0",
            "base_content_sha256": _sha256(active_path),
            "candidate_version": "1.0.1",
            "candidate_content_sha256": "b" * 64,
        },
        "observation": {
            "category": "MISSING_GUARDRAIL",
            "summary": "A sanitized deterministic fixture exposed a gap.",
            "sanitized": True,
            "evidence": [
                {
                    "kind": "TEST_RUN",
                    "reference": "tests/unit/test_cpr.py",
                    "fingerprint": fingerprint,
                    "summary": "Completed-period boundary fixture.",
                    "point_in_time_safe": True,
                }
            ],
        },
        "proposal": {
            "summary": "Clarify completed-period handling.",
            "rationale": "The smallest change removes an ambiguous instruction.",
            "changed_decisions": ["Require a completed source period."],
            "non_goals": ["Do not change deterministic CPR calculations."],
        },
        "risk": {
            "level": "LOW",
            "leakage_risk": "No new market data is introduced.",
            "changes_deterministic_logic": False,
            "changes_provider_behavior": False,
            "expands_permissions": False,
            "mitigations": ["Run completed-period regression fixtures."],
        },
        "evaluation_plan": {
            "required_suites": ["tests/unit/test_cpr.py"],
            "requires_live_validation": False,
            "success_criteria": ["No look-ahead regression."],
            "failure_criteria": ["Any incomplete period becomes eligible."],
        },
        "evaluation_results": [],
        "approval": {
            "required": True,
            "status": "PENDING",
            "approved_by": None,
            "approved_at": None,
            "approved_candidate_sha256": None,
        },
        "promotion": None,
        "rollback": {
            "target_version": "1.0.0",
            "trigger": "Any completed-period regression.",
            "status": "READY",
            "executed_at": None,
            "executed_by": None,
        },
    }


class SkillCandidateValidatorTests(unittest.TestCase):
    def test_valid_draft_is_deterministic_and_read_only(self):
        candidate = _candidate()
        before = copy.deepcopy(candidate)

        first = validator.validate_candidate(candidate, bundle_root=BUNDLE_ROOT)
        second = validator.validate_candidate(candidate, bundle_root=BUNDLE_ROOT)

        self.assertEqual(candidate, before)
        self.assertEqual(first, second)
        self.assertEqual(first.status, "DRAFT")
        self.assertEqual(len(first.record_sha256), 64)

    def test_rejects_permission_expansion_without_echoing_record_value(self):
        candidate = _candidate()
        candidate["risk"]["expands_permissions"] = True
        candidate["proposal"]["rationale"] = "secret-value-must-not-appear"

        with self.assertRaises(validator.CandidateValidationError) as raised:
            validator.validate_candidate(candidate, bundle_root=BUNDLE_ROOT)

        message = str(raised.exception)
        self.assertIn("schema constraint", message)
        self.assertNotIn("secret-value-must-not-appear", message)

    def test_rejects_stale_or_tampered_active_skill_hash(self):
        candidate = _candidate()
        candidate["target"]["base_content_sha256"] = "c" * 64

        with self.assertRaises(validator.CandidateValidationError) as raised:
            validator.validate_candidate(candidate, bundle_root=BUNDLE_ROOT)

        self.assertIn("base content hash", str(raised.exception))

    def test_rejects_non_incrementing_candidate_version(self):
        candidate = _candidate()
        candidate["target"]["candidate_version"] = "1.0.0"

        with self.assertRaises(validator.CandidateValidationError) as raised:
            validator.validate_candidate(candidate, bundle_root=BUNDLE_ROOT)

        self.assertIn("greater than", str(raised.exception))

    def test_approved_state_requires_passing_evaluation_and_matching_hash(self):
        candidate = _candidate()
        candidate["status"] = "APPROVED"
        candidate["approval"].update(
            {
                "status": "APPROVED",
                "approved_by": "maintainer",
                "approved_at": "2026-09-06T13:00:00+05:30",
                "approved_candidate_sha256": "c" * 64,
            }
        )

        with self.assertRaises(validator.CandidateValidationError) as raised:
            validator.validate_candidate(candidate, bundle_root=BUNDLE_ROOT)

        self.assertIn("passing evaluations", str(raised.exception))
        self.assertIn("approval hash", str(raised.exception))

    def test_accepts_human_approved_candidate_with_passing_evaluation(self):
        candidate = _candidate()
        candidate["status"] = "APPROVED"
        candidate["evaluation_results"] = [
            {
                "status": "PASSED",
                "evaluated_at": "2026-09-06T12:30:00+05:30",
                "evidence": candidate["observation"]["evidence"][0],
                "summary": "Targeted and regression checks passed.",
            }
        ]
        candidate["approval"].update(
            {
                "status": "APPROVED",
                "approved_by": "maintainer",
                "approved_at": "2026-09-06T13:00:00+05:30",
                "approved_candidate_sha256": candidate["target"][
                    "candidate_content_sha256"
                ],
            }
        )

        result = validator.validate_candidate(candidate, bundle_root=BUNDLE_ROOT)

        self.assertEqual(result.status, "APPROVED")

    def test_rejects_false_rollback_target_and_unapproved_promotion(self):
        candidate = _candidate()
        candidate["rollback"]["target_version"] = "0.9.0"
        candidate["promotion"] = {
            "promoted_at": "2026-09-06T13:30:00+05:30",
            "promoted_by": "agent",
            "commit": "abcdef1",
            "previous_bundle_sha256": "d" * 64,
            "promoted_bundle_sha256": "e" * 64,
        }

        with self.assertRaises(validator.CandidateValidationError) as raised:
            validator.validate_candidate(candidate, bundle_root=BUNDLE_ROOT)

        message = str(raised.exception)
        self.assertIn("rollback target", message)
        self.assertIn("pre-promotion", message)

    def test_cli_reports_valid_record_without_modifying_it(self):
        candidate = _candidate()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.json"
            serialized = json.dumps(candidate, indent=2)
            path.write_text(serialized, encoding="utf-8")

            completed = subprocess.run(
                [sys.executable, str(VALIDATOR_PATH), str(path)],
                cwd=REPOSITORY_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("VALID candidate_id=skill-change-technical-cpr-001", completed.stdout)
            self.assertEqual(path.read_text(encoding="utf-8"), serialized)


if __name__ == "__main__":
    unittest.main()
