import unittest

from pydantic import ValidationError

from app.models.runtime_skill import (
    DEFAULT_RUNTIME_SKILLS,
    RuntimeAbstentionReason,
    RuntimeAgentRole,
    RuntimeCitationNamespace,
    RuntimeEvidenceBoundary,
    RuntimeSkillContract,
    RuntimeSkillRegistry,
)


def _skill(**overrides: object) -> RuntimeSkillContract:
    values: dict[str, object] = {
        "skill_id": "jarvis.test_runtime_skill.v1",
        "agent_role": RuntimeAgentRole.BULL,
        "version": "1.0.0",
        "purpose": "Test one grounded runtime role.",
        "instructions": ("Use only released evidence.",),
        "allowed_evidence": (
            RuntimeEvidenceBoundary.JUDGE_RELEASED_TECHNICAL,
        ),
        "required_citations": (
            RuntimeCitationNamespace.QUALIFIED_TECHNICAL_EVIDENCE,
        ),
        "output_schema_id": "jarvis.test_output.v1",
        "abstain_on": (RuntimeAbstentionReason.MISSING_EVIDENCE,),
    }
    values.update(overrides)
    return RuntimeSkillContract(**values)


class RuntimeSkillContractTests(unittest.TestCase):
    def test_default_registry_defines_each_runtime_role_once(self) -> None:
        self.assertEqual(
            {skill.agent_role for skill in DEFAULT_RUNTIME_SKILLS.skills},
            set(RuntimeAgentRole),
        )

    def test_default_skills_are_provider_neutral_and_immutable(self) -> None:
        for skill in DEFAULT_RUNTIME_SKILLS.skills:
            with self.subTest(role=skill.agent_role):
                self.assertIs(skill.provider_neutral, True)
                self.assertIs(skill.can_calculate_evidence, False)
                self.assertIs(skill.can_modify_evidence, False)
                self.assertEqual(len(skill.content_sha256), 64)

    def test_technical_agents_require_qualified_citations(self) -> None:
        for role in (
            RuntimeAgentRole.BULL,
            RuntimeAgentRole.BEAR,
            RuntimeAgentRole.JUDGE,
        ):
            with self.subTest(role=role):
                skill = DEFAULT_RUNTIME_SKILLS.skill_for(role)
                self.assertEqual(
                    skill.required_citations,
                    (
                        RuntimeCitationNamespace.
                        QUALIFIED_TECHNICAL_EVIDENCE,
                    ),
                )
                self.assertIn(
                    RuntimeEvidenceBoundary.JUDGE_RELEASED_TECHNICAL,
                    skill.allowed_evidence,
                )

    def test_fundamental_analyst_uses_cached_documents(self) -> None:
        skill = DEFAULT_RUNTIME_SKILLS.skill_for(
            RuntimeAgentRole.FUNDAMENTAL_ANALYST
        )
        self.assertEqual(
            skill.allowed_evidence,
            (RuntimeEvidenceBoundary.CACHED_STRUCTURED_FUNDAMENTALS,),
        )
        self.assertEqual(
            skill.required_citations,
            (RuntimeCitationNamespace.STRUCTURED_DOCUMENT_REFERENCE,),
        )

    def test_skill_hash_changes_only_with_policy_content(self) -> None:
        original = _skill()
        equivalent = _skill()
        changed = _skill(
            instructions=("Use a different bounded instruction.",)
        )
        self.assertEqual(original.content_sha256, equivalent.content_sha256)
        self.assertNotEqual(original.content_sha256, changed.content_sha256)

    def test_skill_rejects_invalid_policy_content(self) -> None:
        cases = (
            (
                "instructions",
                ("valid", "valid"),
                "instructions must be unique",
            ),
            (
                "allowed_evidence",
                (
                    RuntimeEvidenceBoundary.JUDGE_RELEASED_TECHNICAL,
                    RuntimeEvidenceBoundary.JUDGE_RELEASED_TECHNICAL,
                ),
                "evidence boundaries must be unique",
            ),
            ("version", "v1", "string_pattern_mismatch"),
        )
        for field, value, message in cases:
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValidationError, message):
                    _skill(**{field: value})

    def test_registry_rejects_duplicate_or_missing_roles(self) -> None:
        with self.assertRaisesRegex(
            ValidationError,
            "agent roles must be unique",
        ):
            RuntimeSkillRegistry(
                skills=(
                    _skill(skill_id="jarvis.first_skill.v1"),
                    _skill(skill_id="jarvis.second_skill.v1"),
                )
            )
        with self.assertRaisesRegex(
            ValidationError,
            "must define every role",
        ):
            RuntimeSkillRegistry(skills=(_skill(),))

    def test_contract_is_immutable(self) -> None:
        skill = _skill()
        with self.assertRaisesRegex(ValidationError, "frozen"):
            skill.version = "2.0.0"
