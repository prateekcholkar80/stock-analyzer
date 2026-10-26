import unittest

from pydantic import ValidationError

from app.models.fundamental_framework_skill import (
    FundamentalFrameworkSkillAbstentionReason,
    FundamentalFrameworkSkillContract,
    FundamentalFrameworkSkillRegistry,
    REQUIRED_FRAMEWORK_SKILL_ABSTENTIONS,
    REQUIRED_FRAMEWORK_SKILL_CITATIONS,
    REQUIRED_FRAMEWORK_SKILL_EVIDENCE,
)
from app.models.fundamental_frameworks import FundamentalFramework


def _contract(
    framework: FundamentalFramework = (
        FundamentalFramework.JHUNJHUNWALA_STYLE
    ),
    **overrides: object,
) -> FundamentalFrameworkSkillContract:
    values: dict[str, object] = {
        "skill_id": f"jarvis.fundamental_framework.{framework.value}.v1",
        "framework": framework,
        "version": "1.0.0",
        "purpose": "Explain released deterministic framework evidence.",
        "score_policy_id": f"jarvis.framework_score.{framework.value}",
        "score_policy_version": "1.0.0",
        "doctrine_id": (
            "jarvis.fundamental_framework_doctrine."
            f"{framework.value}.v1"
        ),
        "doctrine_version": "1.0.0",
        "allowed_evidence": REQUIRED_FRAMEWORK_SKILL_EVIDENCE,
        "required_citations": REQUIRED_FRAMEWORK_SKILL_CITATIONS,
        "abstain_on": REQUIRED_FRAMEWORK_SKILL_ABSTENTIONS,
    }
    values.update(overrides)
    return FundamentalFrameworkSkillContract(**values)


class FundamentalFrameworkSkillContractTests(unittest.TestCase):
    def test_contract_is_provider_neutral_explanatory_and_shadow_only(
        self,
    ) -> None:
        contract = _contract()

        self.assertIs(contract.shadow_only, True)
        self.assertIs(contract.provider_neutral, True)
        self.assertIs(contract.can_select_framework, False)
        self.assertIs(contract.can_calculate_evidence, False)
        self.assertIs(contract.can_modify_evidence, False)
        self.assertIs(contract.can_override_mandatory_findings, False)
        self.assertIs(contract.can_issue_trade_decision, False)

    def test_contract_requires_exact_framework_and_score_policy_binding(
        self,
    ) -> None:
        cases = (
            (
                "skill_id",
                "jarvis.fundamental_framework.buffett.v1",
                "ID must match",
            ),
            (
                "score_policy_id",
                "jarvis.framework_score.lynch",
                "score policy must match",
            ),
            (
                "doctrine_id",
                "jarvis.fundamental_framework_doctrine.lynch.v1",
                "doctrine ID must match",
            ),
        )
        for field, value, message in cases:
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValidationError, message):
                    _contract(**{field: value})

    def test_contract_requires_complete_canonical_evidence_surfaces(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            ValidationError,
            "evidence must contain every required surface",
        ):
            _contract(
                allowed_evidence=tuple(
                    reversed(REQUIRED_FRAMEWORK_SKILL_EVIDENCE)
                )
            )

    def test_contract_requires_complete_canonical_citation_namespaces(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            ValidationError,
            "citations must contain every required namespace",
        ):
            _contract(
                required_citations=tuple(
                    reversed(REQUIRED_FRAMEWORK_SKILL_CITATIONS)
                )
            )

    def test_contract_requires_every_fail_closed_reason(self) -> None:
        reordered = (
            FundamentalFrameworkSkillAbstentionReason.MISSING_EVIDENCE,
            *(
                reason
                for reason in REQUIRED_FRAMEWORK_SKILL_ABSTENTIONS
                if reason
                is not FundamentalFrameworkSkillAbstentionReason.MISSING_EVIDENCE
            ),
        )
        with self.assertRaisesRegex(
            ValidationError,
            "abstentions must contain every fail-closed reason",
        ):
            _contract(abstain_on=reordered)

    def test_doctrine_identity_and_version_are_atomic(self) -> None:
        for field in ("doctrine_id", "doctrine_version"):
            with self.subTest(missing=field):
                with self.assertRaisesRegex(
                    ValidationError,
                    "must be supplied together",
                ):
                    _contract(**{field: None})

    def test_contract_is_immutable_and_hash_is_deterministic(self) -> None:
        original = _contract()
        equivalent = _contract()
        changed = _contract(purpose="Explain another bounded observation.")

        self.assertEqual(original.content_sha256, equivalent.content_sha256)
        self.assertEqual(len(original.content_sha256), 64)
        self.assertNotEqual(original.content_sha256, changed.content_sha256)
        with self.assertRaisesRegex(ValidationError, "frozen"):
            original.version = "2.0.0"

    def test_contract_rejects_capability_escalation(self) -> None:
        for field in (
            "shadow_only",
            "provider_neutral",
            "can_select_framework",
            "can_calculate_evidence",
            "can_modify_evidence",
            "can_override_mandatory_findings",
            "can_issue_trade_decision",
        ):
            with self.subTest(field=field):
                unsafe_value = False if field in {
                    "shadow_only",
                    "provider_neutral",
                } else True
                with self.assertRaises(ValidationError):
                    _contract(**{field: unsafe_value})

    def test_registry_supports_extensible_partial_registration(self) -> None:
        registry = FundamentalFrameworkSkillRegistry(
            skills=(
                _contract(FundamentalFramework.BUFFETT),
                _contract(FundamentalFramework.JHUNJHUNWALA_STYLE),
            )
        )

        selected = registry.skill_for(
            FundamentalFramework.JHUNJHUNWALA_STYLE
        )
        self.assertEqual(
            selected.framework,
            FundamentalFramework.JHUNJHUNWALA_STYLE,
        )
        self.assertEqual(len(registry.content_sha256), 64)
        with self.assertRaisesRegex(ValueError, "is not configured"):
            registry.skill_for(FundamentalFramework.LYNCH)

    def test_registry_rejects_duplicate_and_noncanonical_frameworks(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValidationError, "must be unique"):
            FundamentalFrameworkSkillRegistry(
                skills=(_contract(), _contract())
            )
        with self.assertRaisesRegex(ValidationError, "canonical framework"):
            FundamentalFrameworkSkillRegistry(
                skills=(
                    _contract(FundamentalFramework.LYNCH),
                    _contract(FundamentalFramework.BUFFETT),
                )
            )


if __name__ == "__main__":
    unittest.main()
