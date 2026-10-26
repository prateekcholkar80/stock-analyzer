"""Fail-closed loader for approved fundamental framework skills."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from hmac import compare_digest
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from yaml.constructor import ConstructorError
from yaml.events import AliasEvent
from yaml.nodes import MappingNode

from app.exceptions import ConfigurationError
from app.models.fundamental_framework_skill import (
    FundamentalFrameworkSkillContract,
    FundamentalFrameworkSkillRegistry,
)
from app.models.fundamental_frameworks import FundamentalFramework


_MAX_MANIFEST_BYTES = 128 * 1024
_MAX_POLICY_BYTES = 64 * 1024
_MAX_SKILL_BYTES = 128 * 1024
_MAX_DOCTRINE_BYTES = 256 * 1024
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class FundamentalFrameworkSkillLoadError(ConfigurationError):
    """Raised when a framework-skill release cannot be loaded atomically."""


class _StrictSafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects aliases and duplicate mapping keys."""

    def compose_node(self, parent: object, index: object) -> object:
        if self.check_event(AliasEvent):
            raise ConstructorError(
                None,
                None,
                "YAML aliases are not permitted",
                self.peek_event().start_mark,
            )
        return super().compose_node(parent, index)

    def construct_mapping(
        self,
        node: MappingNode,
        deep: bool = False,
    ) -> dict[object, object]:
        self.flatten_mapping(node)
        mapping: dict[object, object] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "unhashable mapping key",
                    key_node.start_mark,
                ) from exc
            if duplicate:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "duplicate mapping key",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


class _ManifestModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class _Approval(_ManifestModel):
    status: Literal["approved"]
    scope: Literal["exact_sha256"]
    approved_on: date


class _HashedFileEntry(_ManifestModel):
    path: str = Field(min_length=1)
    content_sha256: str = Field(pattern=_SHA256_PATTERN)


class _DoctrineEntry(_HashedFileEntry):
    doctrine_id: str = Field(min_length=1)
    version: str = Field(min_length=1)


class _SkillEntry(_HashedFileEntry):
    skill_id: str = Field(min_length=1)
    framework: str = Field(min_length=1)
    version: str = Field(min_length=1)
    contract_schema: str = Field(min_length=1)
    output_schema_id: str = Field(min_length=1)
    score_policy_id: str = Field(min_length=1)
    score_policy_version: str = Field(min_length=1)
    doctrine: _DoctrineEntry | None = None
    approval_status: Literal["approved"]


class _FrameworkSkillManifest(_ManifestModel):
    schema_version: Literal[
        "jarvis.fundamental_framework_skill_manifest.v1"
    ]
    registry_contract: Literal[
        "jarvis.fundamental_framework_skill_registry.v1"
    ]
    registry_content_sha256: str = Field(pattern=_SHA256_PATTERN)
    load_policy: Literal["fail_closed"]
    provider_neutral: Literal[True]
    approval: _Approval
    shared_policy: _HashedFileEntry
    skills: list[_SkillEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_entries(self) -> Self:
        for label, values in (
            ("frameworks", [entry.framework for entry in self.skills]),
            ("skill IDs", [entry.skill_id for entry in self.skills]),
            ("paths", [entry.path for entry in self.skills]),
        ):
            if len(values) != len(set(values)):
                raise ValueError(
                    f"framework-skill manifest {label} must be unique"
                )
        doctrine_paths = tuple(
            entry.doctrine.path
            for entry in self.skills
            if entry.doctrine is not None
        )
        if len(doctrine_paths) != len(set(doctrine_paths)):
            raise ValueError(
                "framework-skill manifest doctrine paths must be unique"
            )
        return self


class _SkillMetadata(_ManifestModel):
    framework_skill_id: str = Field(min_length=1)
    framework: str = Field(min_length=1)
    version: str = Field(min_length=1)
    contract_schema: str = Field(min_length=1)
    output_schema_id: str = Field(min_length=1)
    score_policy_id: str = Field(min_length=1)
    score_policy_version: str = Field(min_length=1)
    shared_policy: str = Field(min_length=1)
    doctrine_id: str | None = Field(default=None, min_length=1)
    doctrine_version: str | None = Field(default=None, min_length=1)
    doctrine: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_doctrine_metadata(self) -> Self:
        fields = (
            self.doctrine_id is not None,
            self.doctrine_version is not None,
            self.doctrine is not None,
        )
        if len(set(fields)) != 1:
            raise ValueError(
                "framework-skill doctrine metadata must be supplied together"
            )
        return self


class _SkillFrontmatter(_ManifestModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    metadata: _SkillMetadata


@dataclass(frozen=True, slots=True)
class LoadedFundamentalFrameworkSkill:
    """Verified framework instructions paired with their exact contract."""

    contract: FundamentalFrameworkSkillContract
    skill_name: str
    description: str
    framework_instructions: str
    shared_policy: str
    doctrine: str | None
    skill_content_sha256: str
    shared_policy_content_sha256: str
    doctrine_content_sha256: str | None
    prompt_content_sha256: str

    @property
    def system_instructions(self) -> str:
        parts = [self.shared_policy]
        if self.doctrine is not None:
            parts.append(self.doctrine)
        parts.append(self.framework_instructions)
        return "\n\n".join(parts)


@dataclass(frozen=True, slots=True)
class LoadedFundamentalFrameworkSkillSet:
    """All-or-nothing verified framework-skill release."""

    manifest_sha256: str
    registry_content_sha256: str
    skills: tuple[LoadedFundamentalFrameworkSkill, ...]

    def skill_for(
        self,
        framework: FundamentalFramework,
    ) -> LoadedFundamentalFrameworkSkill:
        for skill in self.skills:
            if skill.contract.framework is framework:
                return skill
        raise FundamentalFrameworkSkillLoadError(
            "fundamental framework skill is not available"
        )


class FundamentalFrameworkSkillLoader:
    """Load one exact-hash framework bundle or expose no framework skills."""

    def __init__(
        self,
        manifest_path: Path,
        *,
        registry: FundamentalFrameworkSkillRegistry,
    ) -> None:
        if not isinstance(manifest_path, Path):
            raise TypeError("framework-skill manifest path must be a Path")
        if not isinstance(registry, FundamentalFrameworkSkillRegistry):
            raise TypeError("framework-skill registry must be validated")
        self._manifest_path = manifest_path
        self._registry = registry

    def load(self) -> LoadedFundamentalFrameworkSkillSet:
        """Validate every artifact before returning an immutable skill set."""
        try:
            return self._load()
        except FundamentalFrameworkSkillLoadError:
            raise
        except (OSError, UnicodeError, yaml.YAMLError, ValidationError) as exc:
            raise FundamentalFrameworkSkillLoadError(
                "fundamental framework-skill bundle validation failed"
            ) from exc

    def _load(self) -> LoadedFundamentalFrameworkSkillSet:
        manifest_path = self._manifest_path.resolve(strict=True)
        if not manifest_path.is_file():
            raise FundamentalFrameworkSkillLoadError(
                "framework-skill manifest is not a file"
            )
        manifest_bytes, manifest_text = _read_text(
            manifest_path,
            maximum_bytes=_MAX_MANIFEST_BYTES,
            label="manifest",
        )
        manifest = _FrameworkSkillManifest.model_validate(
            _load_yaml(manifest_text)
        )

        bundle_root = manifest_path.parent.parent.resolve(strict=True)
        if manifest_path.parent == bundle_root:
            raise FundamentalFrameworkSkillLoadError(
                "framework-skill manifest requires an owned bundle root"
            )
        if not compare_digest(
            manifest.registry_content_sha256,
            self._registry.content_sha256,
        ):
            raise FundamentalFrameworkSkillLoadError(
                "framework-skill manifest registry hash mismatch"
            )

        expected_frameworks = tuple(
            skill.framework.value for skill in self._registry.skills
        )
        actual_frameworks = tuple(entry.framework for entry in manifest.skills)
        if actual_frameworks != expected_frameworks:
            raise FundamentalFrameworkSkillLoadError(
                "framework-skill manifest must match its complete registry"
            )

        shared_path = _resolve_owned_file(
            bundle_root,
            manifest.shared_policy.path,
        )
        _, shared_text = _read_verified_text(
            shared_path,
            expected_sha256=manifest.shared_policy.content_sha256,
            maximum_bytes=_MAX_POLICY_BYTES,
            label="shared policy",
        )

        loaded = tuple(
            self._load_skill(
                bundle_root=bundle_root,
                shared_path=shared_path,
                shared_text=shared_text,
                shared_sha256=manifest.shared_policy.content_sha256,
                entry=entry,
            )
            for entry in manifest.skills
        )
        return LoadedFundamentalFrameworkSkillSet(
            manifest_sha256=sha256(manifest_bytes).hexdigest(),
            registry_content_sha256=self._registry.content_sha256,
            skills=loaded,
        )

    def _load_skill(
        self,
        *,
        bundle_root: Path,
        shared_path: Path,
        shared_text: str,
        shared_sha256: str,
        entry: _SkillEntry,
    ) -> LoadedFundamentalFrameworkSkill:
        try:
            framework = FundamentalFramework(entry.framework)
        except ValueError as exc:
            raise FundamentalFrameworkSkillLoadError(
                "framework-skill manifest contains an unknown framework"
            ) from exc
        try:
            contract = self._registry.skill_for(framework)
        except ValueError as exc:
            raise FundamentalFrameworkSkillLoadError(
                "framework-skill manifest contains an unregistered framework"
            ) from exc
        _validate_entry_contract(entry, contract)

        skill_path = _resolve_owned_file(bundle_root, entry.path)
        _, skill_text = _read_verified_text(
            skill_path,
            expected_sha256=entry.content_sha256,
            maximum_bytes=_MAX_SKILL_BYTES,
            label="skill",
        )
        frontmatter_text, framework_instructions = _split_skill(skill_text)
        frontmatter = _SkillFrontmatter.model_validate(
            _load_yaml(frontmatter_text)
        )

        doctrine_path: Path | None = None
        doctrine_text: str | None = None
        doctrine_sha256: str | None = None
        if entry.doctrine is not None:
            doctrine_path = _resolve_owned_file(
                bundle_root,
                entry.doctrine.path,
            )
            _, doctrine_text = _read_verified_text(
                doctrine_path,
                expected_sha256=entry.doctrine.content_sha256,
                maximum_bytes=_MAX_DOCTRINE_BYTES,
                label="doctrine",
            )
            doctrine_sha256 = entry.doctrine.content_sha256

        _validate_frontmatter(
            frontmatter=frontmatter,
            entry=entry,
            contract=contract,
            skill_path=skill_path,
            bundle_root=bundle_root,
            shared_path=shared_path,
            doctrine_path=doctrine_path,
        )

        parts = [shared_text]
        if doctrine_text is not None:
            parts.append(doctrine_text)
        parts.append(framework_instructions)
        system_instructions = "\n\n".join(parts)
        return LoadedFundamentalFrameworkSkill(
            contract=contract,
            skill_name=frontmatter.name.strip(),
            description=frontmatter.description.strip(),
            framework_instructions=framework_instructions,
            shared_policy=shared_text,
            doctrine=doctrine_text,
            skill_content_sha256=entry.content_sha256,
            shared_policy_content_sha256=shared_sha256,
            doctrine_content_sha256=doctrine_sha256,
            prompt_content_sha256=sha256(
                system_instructions.encode("utf-8")
            ).hexdigest(),
        )


def _load_yaml(text: str) -> object:
    parsed = yaml.load(text, Loader=_StrictSafeLoader)
    if not isinstance(parsed, dict):
        raise FundamentalFrameworkSkillLoadError(
            "framework-skill YAML must be a mapping"
        )
    return parsed


def _resolve_owned_file(root: Path, relative_path: str) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise FundamentalFrameworkSkillLoadError(
            "framework-skill paths must be relative"
        )
    try:
        resolved = (root / candidate).resolve(strict=True)
    except OSError as exc:
        raise FundamentalFrameworkSkillLoadError(
            "framework-skill file is unavailable"
        ) from exc
    if resolved == root or root not in resolved.parents:
        raise FundamentalFrameworkSkillLoadError(
            "framework-skill path escapes its bundle"
        )
    if not resolved.is_file():
        raise FundamentalFrameworkSkillLoadError(
            "framework-skill path is not a file"
        )
    return resolved


def _read_text(
    path: Path,
    *,
    maximum_bytes: int,
    label: str,
) -> tuple[bytes, str]:
    content = path.read_bytes()
    if not content or len(content) > maximum_bytes:
        raise FundamentalFrameworkSkillLoadError(
            f"framework-skill {label} size is invalid"
        )
    text = content.decode("utf-8")
    if "\x00" in text or not text.strip():
        raise FundamentalFrameworkSkillLoadError(
            f"framework-skill {label} content is invalid"
        )
    return content, text


def _read_verified_text(
    path: Path,
    *,
    expected_sha256: str,
    maximum_bytes: int,
    label: str,
) -> tuple[bytes, str]:
    content, text = _read_text(
        path,
        maximum_bytes=maximum_bytes,
        label=label,
    )
    if not compare_digest(sha256(content).hexdigest(), expected_sha256):
        raise FundamentalFrameworkSkillLoadError(
            f"framework-skill {label} hash mismatch"
        )
    return content, text


def _split_skill(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise FundamentalFrameworkSkillLoadError(
            "framework skill has no frontmatter"
        )
    try:
        closing_index = lines.index("---", 1)
    except ValueError as exc:
        raise FundamentalFrameworkSkillLoadError(
            "framework skill frontmatter is unterminated"
        ) from exc
    frontmatter = "\n".join(lines[1:closing_index])
    instructions = "\n".join(lines[closing_index + 1 :]).strip()
    if not frontmatter.strip() or not instructions:
        raise FundamentalFrameworkSkillLoadError(
            "framework skill instructions are empty"
        )
    return frontmatter, instructions


def _validate_entry_contract(
    entry: _SkillEntry,
    contract: FundamentalFrameworkSkillContract,
) -> None:
    expected_doctrine = None
    if contract.doctrine_id is not None:
        expected_doctrine = (
            contract.doctrine_id,
            contract.doctrine_version,
        )
    actual_doctrine = None
    if entry.doctrine is not None:
        actual_doctrine = (
            entry.doctrine.doctrine_id,
            entry.doctrine.version,
        )
    if (
        entry.skill_id != contract.skill_id
        or entry.framework != contract.framework.value
        or entry.version != contract.version
        or entry.contract_schema != contract.schema_version
        or entry.output_schema_id != contract.output_schema_id
        or entry.score_policy_id != contract.score_policy_id
        or entry.score_policy_version != contract.score_policy_version
        or actual_doctrine != expected_doctrine
    ):
        raise FundamentalFrameworkSkillLoadError(
            "framework-skill manifest does not match its Python contract"
        )


def _validate_frontmatter(
    *,
    frontmatter: _SkillFrontmatter,
    entry: _SkillEntry,
    contract: FundamentalFrameworkSkillContract,
    skill_path: Path,
    bundle_root: Path,
    shared_path: Path,
    doctrine_path: Path | None,
) -> None:
    metadata = frontmatter.metadata
    if (
        metadata.framework_skill_id != entry.skill_id
        or metadata.framework != entry.framework
        or metadata.version != entry.version
        or metadata.contract_schema != entry.contract_schema
        or metadata.output_schema_id != entry.output_schema_id
        or metadata.score_policy_id != entry.score_policy_id
        or metadata.score_policy_version != entry.score_policy_version
    ):
        raise FundamentalFrameworkSkillLoadError(
            "framework-skill frontmatter does not match its manifest"
        )

    referenced_shared_policy = _resolve_skill_reference(
        bundle_root=bundle_root,
        skill_path=skill_path,
        relative_path=metadata.shared_policy,
    )
    if referenced_shared_policy != shared_path:
        raise FundamentalFrameworkSkillLoadError(
            "framework skill references an unapproved shared policy"
        )

    expected_doctrine_metadata = (
        contract.doctrine_id,
        contract.doctrine_version,
    )
    actual_doctrine_metadata = (
        metadata.doctrine_id,
        metadata.doctrine_version,
    )
    if actual_doctrine_metadata != expected_doctrine_metadata:
        raise FundamentalFrameworkSkillLoadError(
            "framework-skill doctrine metadata does not match its contract"
        )
    if metadata.doctrine is None:
        if doctrine_path is not None:
            raise FundamentalFrameworkSkillLoadError(
                "framework-skill doctrine reference is missing"
            )
    else:
        referenced_doctrine = _resolve_skill_reference(
            bundle_root=bundle_root,
            skill_path=skill_path,
            relative_path=metadata.doctrine,
        )
        if doctrine_path is None or referenced_doctrine != doctrine_path:
            raise FundamentalFrameworkSkillLoadError(
                "framework skill references an unapproved doctrine"
            )


def _resolve_skill_reference(
    *,
    bundle_root: Path,
    skill_path: Path,
    relative_path: str,
) -> Path:
    try:
        candidate = (skill_path.parent / relative_path).resolve(strict=True)
    except OSError as exc:
        raise FundamentalFrameworkSkillLoadError(
            "framework-skill reference is unavailable"
        ) from exc
    if candidate == bundle_root or bundle_root not in candidate.parents:
        raise FundamentalFrameworkSkillLoadError(
            "framework-skill reference escapes its bundle"
        )
    if not candidate.is_file():
        raise FundamentalFrameworkSkillLoadError(
            "framework-skill reference is not a file"
        )
    return candidate
