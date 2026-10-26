from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import yaml

from app.models.runtime_skill import DEFAULT_RUNTIME_SKILLS, RuntimeAgentRole
from app.runtime_skill_loader import RuntimeSkillLoadError, RuntimeSkillLoader


class RuntimeSkillLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.shared_path = self.root / "shared" / "evidence-grounding.md"
        self.shared_path.parent.mkdir()
        self.shared_path.write_text(
            "# Shared policy\n\nUse only released evidence.\n",
            encoding="utf-8",
        )
        self.entries = []
        for contract in DEFAULT_RUNTIME_SKILLS.skills:
            folder = self.root / f"jarvis-{contract.agent_role.value}-runtime"
            folder.mkdir()
            skill_path = folder / "SKILL.md"
            skill_path.write_text(
                self._skill_text(contract, folder.name),
                encoding="utf-8",
            )
            self.entries.append(
                {
                    "skill_id": contract.skill_id,
                    "agent_role": contract.agent_role.value,
                    "version": contract.version,
                    "path": str(skill_path.relative_to(self.root)),
                    "content_sha256": self._hash(skill_path),
                    "contract_schema": contract.schema_version,
                    "output_schema_id": contract.output_schema_id,
                    "approval_status": "approved",
                }
            )
        self.manifest_path = self.root / "manifest.yaml"
        self._write_manifest()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    @staticmethod
    def _hash(path: Path) -> str:
        return sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _skill_text(contract: object, name: str) -> str:
        return (
            "---\n"
            f"name: {name}\n"
            "description: A bounded runtime research skill.\n"
            "metadata:\n"
            f"  runtime_skill_id: {contract.skill_id}\n"
            f"  agent_role: {contract.agent_role.value}\n"
            f"  version: {contract.version}\n"
            f"  contract_schema: {contract.schema_version}\n"
            f"  output_schema_id: {contract.output_schema_id}\n"
            "  shared_policy: ../shared/evidence-grounding.md\n"
            "---\n\n"
            "# Role instructions\n\nUse exact citations.\n"
        )

    def _manifest(self) -> dict[str, object]:
        return {
            "schema_version": "jarvis.runtime_skill_manifest.v1",
            "registry_contract": "jarvis.runtime_skill_registry.v1",
            "load_policy": "fail_closed",
            "provider_neutral": True,
            "approval": {
                "status": "approved",
                "scope": "exact_sha256",
                "approved_on": "2026-09-08",
            },
            "shared_policy": {
                "path": str(self.shared_path.relative_to(self.root)),
                "content_sha256": self._hash(self.shared_path),
            },
            "skills": self.entries,
        }

    def _write_manifest(self, manifest: dict[str, object] | None = None) -> None:
        self.manifest_path.write_text(
            yaml.safe_dump(manifest or self._manifest(), sort_keys=False),
            encoding="utf-8",
        )

    def test_loads_complete_verified_skill_set(self) -> None:
        loaded = RuntimeSkillLoader(self.manifest_path).load()

        self.assertEqual(len(loaded.skills), 4)
        self.assertEqual(len(loaded.manifest_sha256), 64)
        bull = loaded.skill_for(RuntimeAgentRole.BULL)
        self.assertIn("Use only released evidence", bull.system_instructions)
        self.assertIn("Use exact citations", bull.system_instructions)
        self.assertEqual(len(bull.prompt_content_sha256), 64)
        self.assertEqual(
            bull.skill_content_sha256,
            next(
                entry["content_sha256"]
                for entry in self.entries
                if entry["agent_role"] == "bull"
            ),
        )

    def test_rejects_missing_manifest_without_partial_result(self) -> None:
        with self.assertRaisesRegex(
            RuntimeSkillLoadError,
            "bundle validation failed",
        ):
            RuntimeSkillLoader(self.root / "missing.yaml").load()

    def test_rejects_tampered_skill_hash(self) -> None:
        bull_path = self.root / self.entries[0]["path"]
        bull_path.write_text(
            bull_path.read_text(encoding="utf-8") + "Tampered.\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(RuntimeSkillLoadError, "hash mismatch"):
            RuntimeSkillLoader(self.manifest_path).load()

    def test_rejects_tampered_shared_policy_hash(self) -> None:
        self.shared_path.write_text("changed policy", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeSkillLoadError, "hash mismatch"):
            RuntimeSkillLoader(self.manifest_path).load()

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
                    RuntimeSkillLoadError,
                    "bundle validation failed",
                ):
                    RuntimeSkillLoader(self.manifest_path).load()

    def test_rejects_missing_and_duplicate_roles(self) -> None:
        for mutation in ("missing", "duplicate"):
            with self.subTest(mutation=mutation):
                manifest = self._manifest()
                if mutation == "missing":
                    manifest["skills"] = manifest["skills"][:-1]
                else:
                    manifest["skills"][1]["agent_role"] = "bull"
                self._write_manifest(manifest)
                with self.assertRaises(RuntimeSkillLoadError):
                    RuntimeSkillLoader(self.manifest_path).load()

    def test_rejects_path_escape(self) -> None:
        outside = self.root.parent / "outside-runtime-skill.md"
        outside.write_text("not approved", encoding="utf-8")
        self.addCleanup(outside.unlink, missing_ok=True)
        manifest = self._manifest()
        manifest["skills"][0]["path"] = "../outside-runtime-skill.md"
        manifest["skills"][0]["content_sha256"] = self._hash(outside)
        self._write_manifest(manifest)

        with self.assertRaisesRegex(RuntimeSkillLoadError, "escapes"):
            RuntimeSkillLoader(self.manifest_path).load()

    def test_rejects_manifest_python_contract_mismatch(self) -> None:
        manifest = self._manifest()
        manifest["skills"][0]["version"] = "2.0.0"
        self._write_manifest(manifest)

        with self.assertRaisesRegex(RuntimeSkillLoadError, "Python contract"):
            RuntimeSkillLoader(self.manifest_path).load()

    def test_rejects_frontmatter_manifest_mismatch_after_reapproval(self) -> None:
        entry = self.entries[0]
        skill_path = self.root / entry["path"]
        skill_path.write_text(
            skill_path.read_text(encoding="utf-8").replace(
                "agent_role: bull",
                "agent_role: bear",
            ),
            encoding="utf-8",
        )
        entry["content_sha256"] = self._hash(skill_path)
        self._write_manifest()

        with self.assertRaisesRegex(RuntimeSkillLoadError, "frontmatter"):
            RuntimeSkillLoader(self.manifest_path).load()

    def test_rejects_unapproved_shared_policy_reference(self) -> None:
        entry = self.entries[0]
        skill_path = self.root / entry["path"]
        alternate = self.root / "shared" / "alternate.md"
        alternate.write_text("alternate policy", encoding="utf-8")
        skill_path.write_text(
            skill_path.read_text(encoding="utf-8").replace(
                "../shared/evidence-grounding.md",
                "../shared/alternate.md",
            ),
            encoding="utf-8",
        )
        entry["content_sha256"] = self._hash(skill_path)
        self._write_manifest()

        with self.assertRaisesRegex(RuntimeSkillLoadError, "unapproved"):
            RuntimeSkillLoader(self.manifest_path).load()

    def test_rejects_duplicate_yaml_keys_and_aliases(self) -> None:
        valid_manifest = self.manifest_path.read_text(encoding="utf-8")
        for invalid in (
            valid_manifest.replace(
                "load_policy: fail_closed",
                "load_policy: fail_closed\nload_policy: fail_closed",
            ),
            valid_manifest.replace(
                "status: approved",
                "status: &approved approved",
                1,
            ).replace(
                "approval_status: approved",
                "approval_status: *approved",
                1,
            ),
        ):
            with self.subTest():
                self.manifest_path.write_text(invalid, encoding="utf-8")
                with self.assertRaisesRegex(
                    RuntimeSkillLoadError,
                    "bundle validation failed",
                ):
                    RuntimeSkillLoader(self.manifest_path).load()

    def test_rejects_oversized_or_invalid_utf8_skill(self) -> None:
        entry = self.entries[0]
        skill_path = self.root / entry["path"]
        for content in (b"x" * (128 * 1024 + 1), b"\xff\xfe"):
            with self.subTest(size=len(content)):
                skill_path.write_bytes(content)
                entry["content_sha256"] = self._hash(skill_path)
                self._write_manifest()
                with self.assertRaises(RuntimeSkillLoadError):
                    RuntimeSkillLoader(self.manifest_path).load()

    def test_loaded_records_are_immutable(self) -> None:
        loaded = RuntimeSkillLoader(self.manifest_path).load()
        with self.assertRaises(AttributeError):
            loaded.manifest_sha256 = "0" * 64


if __name__ == "__main__":
    unittest.main()
