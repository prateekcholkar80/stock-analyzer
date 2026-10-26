from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import yaml

from app.fundamental_framework_skill_loader import (
    FundamentalFrameworkSkillLoadError,
    FundamentalFrameworkSkillLoader,
)
from app.models.fundamental_framework_skill import (
    FundamentalFrameworkSkillContract,
    FundamentalFrameworkSkillRegistry,
    REQUIRED_FRAMEWORK_SKILL_ABSTENTIONS,
    REQUIRED_FRAMEWORK_SKILL_CITATIONS,
    REQUIRED_FRAMEWORK_SKILL_EVIDENCE,
)
from app.models.fundamental_frameworks import FundamentalFramework


def _contract(
    framework: FundamentalFramework,
    *,
    with_doctrine: bool,
) -> FundamentalFrameworkSkillContract:
    values: dict[str, object] = {
        "skill_id": f"jarvis.fundamental_framework.{framework.value}.v1",
        "framework": framework,
        "version": "1.0.0",
        "purpose": "Explain released deterministic framework evidence.",
        "score_policy_id": f"jarvis.framework_score.{framework.value}",
        "score_policy_version": "1.0.0",
        "allowed_evidence": REQUIRED_FRAMEWORK_SKILL_EVIDENCE,
        "required_citations": REQUIRED_FRAMEWORK_SKILL_CITATIONS,
        "abstain_on": REQUIRED_FRAMEWORK_SKILL_ABSTENTIONS,
    }
    if with_doctrine:
        values.update(
            {
                "doctrine_id": (
                    "jarvis.fundamental_framework_doctrine."
                    f"{framework.value}.v1"
                ),
                "doctrine_version": "1.0.0",
            }
        )
    return FundamentalFrameworkSkillContract(**values)


class FundamentalFrameworkSkillLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.bundle_root = Path(self.temporary_directory.name)
        self.framework_root = self.bundle_root / "fundamental-frameworks"
        self.framework_root.mkdir()
        self.shared_path = self.bundle_root / "shared" / "evidence-grounding.md"
        self.shared_path.parent.mkdir()
        self.shared_path.write_text(
            "# Shared policy\n\nUse only released evidence.\n",
            encoding="utf-8",
        )
        self.registry = FundamentalFrameworkSkillRegistry(
            skills=(
                _contract(FundamentalFramework.BUFFETT, with_doctrine=False),
                _contract(
                    FundamentalFramework.JHUNJHUNWALA_STYLE,
                    with_doctrine=True,
                ),
            )
        )
        self.entries: list[dict[str, object]] = []
        for contract in self.registry.skills:
            folder = self.framework_root / contract.framework.value.replace(
                "_", "-"
            )
            folder.mkdir()
            doctrine_entry = None
            doctrine_path = None
            if contract.doctrine_id is not None:
                doctrine_path = folder / "references" / "framework-doctrine.md"
                doctrine_path.parent.mkdir()
                doctrine_path.write_text(
                    "# Doctrine\n\nExplain no unsupported qualitative facts.\n",
                    encoding="utf-8",
                )
                doctrine_entry = {
                    "doctrine_id": contract.doctrine_id,
                    "version": contract.doctrine_version,
                    "path": str(doctrine_path.relative_to(self.bundle_root)),
                    "content_sha256": self._hash(doctrine_path),
                }
            skill_path = folder / "SKILL.md"
            skill_path.write_text(
                self._skill_text(
                    contract,
                    doctrine_path=doctrine_path,
                ),
                encoding="utf-8",
            )
            self.entries.append(
                {
                    "skill_id": contract.skill_id,
                    "framework": contract.framework.value,
                    "version": contract.version,
                    "path": str(skill_path.relative_to(self.bundle_root)),
                    "content_sha256": self._hash(skill_path),
                    "contract_schema": contract.schema_version,
                    "output_schema_id": contract.output_schema_id,
                    "score_policy_id": contract.score_policy_id,
                    "score_policy_version": contract.score_policy_version,
                    "doctrine": doctrine_entry,
                    "approval_status": "approved",
                }
            )
        self.manifest_path = self.framework_root / "manifest.yaml"
        self._write_manifest()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    @staticmethod
    def _hash(path: Path) -> str:
        return sha256(path.read_bytes()).hexdigest()

    def _skill_text(
        self,
        contract: FundamentalFrameworkSkillContract,
        *,
        doctrine_path: Path | None,
    ) -> str:
        skill_folder = (
            self.framework_root
            / contract.framework.value.replace("_", "-")
        )
        lines = [
            "---",
            f"name: jarvis-{contract.framework.value}-framework",
            "description: A bounded explanatory framework skill.",
            "metadata:",
            f"  framework_skill_id: {contract.skill_id}",
            f"  framework: {contract.framework.value}",
            f"  version: {contract.version}",
            f"  contract_schema: {contract.schema_version}",
            f"  output_schema_id: {contract.output_schema_id}",
            f"  score_policy_id: {contract.score_policy_id}",
            f"  score_policy_version: {contract.score_policy_version}",
            "  shared_policy: ../../shared/evidence-grounding.md",
        ]
        if doctrine_path is not None:
            lines.extend(
                (
                    f"  doctrine_id: {contract.doctrine_id}",
                    f"  doctrine_version: {contract.doctrine_version}",
                    "  doctrine: references/framework-doctrine.md",
                )
            )
        lines.extend(
            (
                "---",
                "",
                "# Framework instructions",
                "",
                "Use exact released citations.",
            )
        )
        return "\n".join(lines) + "\n"

    def _manifest(self) -> dict[str, object]:
        return {
            "schema_version": (
                "jarvis.fundamental_framework_skill_manifest.v1"
            ),
            "registry_contract": (
                "jarvis.fundamental_framework_skill_registry.v1"
            ),
            "registry_content_sha256": self.registry.content_sha256,
            "load_policy": "fail_closed",
            "provider_neutral": True,
            "approval": {
                "status": "approved",
                "scope": "exact_sha256",
                "approved_on": "2026-09-13",
            },
            "shared_policy": {
                "path": str(self.shared_path.relative_to(self.bundle_root)),
                "content_sha256": self._hash(self.shared_path),
            },
            "skills": self.entries,
        }

    def _write_manifest(
        self,
        manifest: dict[str, object] | None = None,
    ) -> None:
        self.manifest_path.write_text(
            yaml.safe_dump(manifest or self._manifest(), sort_keys=False),
            encoding="utf-8",
        )

    def _loader(self) -> FundamentalFrameworkSkillLoader:
        return FundamentalFrameworkSkillLoader(
            self.manifest_path,
            registry=self.registry,
        )

    def test_loads_complete_verified_bundle_with_optional_doctrine(
        self,
    ) -> None:
        loaded = self._loader().load()

        self.assertEqual(len(loaded.skills), 2)
        self.assertEqual(len(loaded.manifest_sha256), 64)
        self.assertEqual(
            loaded.registry_content_sha256,
            self.registry.content_sha256,
        )
        buffett = loaded.skill_for(FundamentalFramework.BUFFETT)
        self.assertIsNone(buffett.doctrine)
        jhunjhunwala = loaded.skill_for(
            FundamentalFramework.JHUNJHUNWALA_STYLE
        )
        self.assertIn("# Doctrine", jhunjhunwala.system_instructions)
        self.assertIn("Use only released evidence", jhunjhunwala.system_instructions)
        self.assertIn("Use exact released citations", jhunjhunwala.system_instructions)
        self.assertEqual(len(jhunjhunwala.prompt_content_sha256), 64)

    def test_rejects_missing_manifest_without_partial_result(self) -> None:
        loader = FundamentalFrameworkSkillLoader(
            self.framework_root / "missing.yaml",
            registry=self.registry,
        )
        with self.assertRaisesRegex(
            FundamentalFrameworkSkillLoadError,
            "bundle validation failed",
        ):
            loader.load()

    def test_rejects_tampered_hashed_artifacts(self) -> None:
        paths = (
            self.shared_path,
            self.bundle_root / str(self.entries[0]["path"]),
            self.bundle_root
            / str(self.entries[1]["doctrine"]["path"]),
        )
        for path in paths:
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(original + b"tampered\n")
                try:
                    with self.assertRaisesRegex(
                        FundamentalFrameworkSkillLoadError,
                        "hash mismatch",
                    ):
                        self._loader().load()
                finally:
                    path.write_bytes(original)

    def test_rejects_registry_hash_or_contract_mismatch(self) -> None:
        for field, value, message in (
            ("registry_content_sha256", "0" * 64, "registry hash mismatch"),
            ("score_policy_version", "2.0.0", "Python contract"),
        ):
            with self.subTest(field=field):
                manifest = self._manifest()
                if field == "registry_content_sha256":
                    manifest[field] = value
                else:
                    manifest["skills"][0][field] = value
                self._write_manifest(manifest)
                with self.assertRaisesRegex(
                    FundamentalFrameworkSkillLoadError,
                    message,
                ):
                    self._loader().load()

    def test_rejects_incomplete_or_duplicate_framework_set(self) -> None:
        for mutation in ("missing", "duplicate"):
            with self.subTest(mutation=mutation):
                manifest = self._manifest()
                if mutation == "missing":
                    manifest["skills"] = manifest["skills"][:-1]
                else:
                    manifest["skills"][1]["framework"] = "buffett"
                self._write_manifest(manifest)
                with self.assertRaises(FundamentalFrameworkSkillLoadError):
                    self._loader().load()

    def test_rejects_unapproved_manifest_or_skill(self) -> None:
        for mutation in ("manifest", "skill"):
            with self.subTest(mutation=mutation):
                manifest = self._manifest()
                if mutation == "manifest":
                    manifest["approval"]["status"] = "candidate"
                else:
                    manifest["skills"][0]["approval_status"] = "candidate"
                self._write_manifest(manifest)
                with self.assertRaisesRegex(
                    FundamentalFrameworkSkillLoadError,
                    "bundle validation failed",
                ):
                    self._loader().load()

    def test_rejects_path_escape_for_every_artifact_kind(self) -> None:
        outside = self.bundle_root.parent / "outside-framework-skill.md"
        outside.write_text("not approved", encoding="utf-8")
        self.addCleanup(outside.unlink, missing_ok=True)
        mutations = (
            ("shared",),
            ("skill",),
            ("doctrine",),
        )
        for (artifact,) in mutations:
            with self.subTest(artifact=artifact):
                manifest = self._manifest()
                escaped = f"../{outside.name}"
                if artifact == "shared":
                    manifest["shared_policy"]["path"] = escaped
                    manifest["shared_policy"]["content_sha256"] = self._hash(
                        outside
                    )
                elif artifact == "skill":
                    manifest["skills"][0]["path"] = escaped
                    manifest["skills"][0]["content_sha256"] = self._hash(
                        outside
                    )
                else:
                    manifest["skills"][1]["doctrine"]["path"] = escaped
                    manifest["skills"][1]["doctrine"][
                        "content_sha256"
                    ] = self._hash(outside)
                self._write_manifest(manifest)
                with self.assertRaisesRegex(
                    FundamentalFrameworkSkillLoadError,
                    "escapes",
                ):
                    self._loader().load()

    def test_rejects_frontmatter_or_reference_mismatch_after_reapproval(
        self,
    ) -> None:
        entry = self.entries[1]
        skill_path = self.bundle_root / str(entry["path"])
        original = skill_path.read_text(encoding="utf-8")
        mutations = (
            ("framework: jhunjhunwala_style", "framework: lynch"),
            (
                "references/framework-doctrine.md",
                "../../shared/evidence-grounding.md",
            ),
        )
        for old, new in mutations:
            with self.subTest(mutation=old):
                skill_path.write_text(original.replace(old, new), encoding="utf-8")
                entry["content_sha256"] = self._hash(skill_path)
                self._write_manifest()
                try:
                    with self.assertRaises(FundamentalFrameworkSkillLoadError):
                        self._loader().load()
                finally:
                    skill_path.write_text(original, encoding="utf-8")
                    entry["content_sha256"] = self._hash(skill_path)

    def test_rejects_duplicate_yaml_keys_and_aliases(self) -> None:
        valid = self.manifest_path.read_text(encoding="utf-8")
        invalid_documents = (
            valid.replace(
                "load_policy: fail_closed",
                "load_policy: fail_closed\nload_policy: fail_closed",
            ),
            valid.replace(
                "status: approved",
                "status: &approved approved",
                1,
            ).replace(
                "approval_status: approved",
                "approval_status: *approved",
                1,
            ),
        )
        for invalid in invalid_documents:
            with self.subTest():
                self.manifest_path.write_text(invalid, encoding="utf-8")
                with self.assertRaisesRegex(
                    FundamentalFrameworkSkillLoadError,
                    "bundle validation failed",
                ):
                    self._loader().load()

    def test_rejects_oversized_or_invalid_utf8_skill(self) -> None:
        entry = self.entries[0]
        skill_path = self.bundle_root / str(entry["path"])
        for content in (b"x" * (128 * 1024 + 1), b"\xff\xfe"):
            with self.subTest(size=len(content)):
                skill_path.write_bytes(content)
                entry["content_sha256"] = self._hash(skill_path)
                self._write_manifest()
                with self.assertRaises(FundamentalFrameworkSkillLoadError):
                    self._loader().load()

    def test_loaded_records_are_immutable(self) -> None:
        loaded = self._loader().load()
        with self.assertRaises(AttributeError):
            loaded.manifest_sha256 = "0" * 64


if __name__ == "__main__":
    unittest.main()
