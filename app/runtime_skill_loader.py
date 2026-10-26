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
from app.models.runtime_skill import (
    DEFAULT_RUNTIME_SKILLS,
    RuntimeAgentRole,
    RuntimeSkillContract,
    RuntimeSkillRegistry,
)


_MAX_MANIFEST_BYTES = 128 * 1024
_MAX_POLICY_BYTES = 64 * 1024
_MAX_SKILL_BYTES = 128 * 1024
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class RuntimeSkillLoadError(ConfigurationError):
    """Raised when the approved runtime-skill bundle fails closed."""


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


class _SharedPolicyEntry(_ManifestModel):
    path: str = Field(min_length=1)
    content_sha256: str = Field(pattern=_SHA256_PATTERN)


class _SkillEntry(_ManifestModel):
    skill_id: str = Field(min_length=1)
    agent_role: str = Field(min_length=1)
    version: str = Field(min_length=1)
    path: str = Field(min_length=1)
    content_sha256: str = Field(pattern=_SHA256_PATTERN)
    contract_schema: str = Field(min_length=1)
    output_schema_id: str = Field(min_length=1)
    approval_status: Literal["approved"]


class _RuntimeSkillManifest(_ManifestModel):
    schema_version: Literal["jarvis.runtime_skill_manifest.v1"]
    registry_contract: Literal["jarvis.runtime_skill_registry.v1"]
    load_policy: Literal["fail_closed"]
    provider_neutral: Literal[True]
    approval: _Approval
    shared_policy: _SharedPolicyEntry
    skills: list[_SkillEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_entries(self) -> Self:
        for label, values in (
            ("roles", [entry.agent_role for entry in self.skills]),
            ("skill IDs", [entry.skill_id for entry in self.skills]),
            ("paths", [entry.path for entry in self.skills]),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"runtime-skill manifest {label} must be unique")
        return self


class _SkillMetadata(_ManifestModel):
    runtime_skill_id: str = Field(min_length=1)
    agent_role: str = Field(min_length=1)
    version: str = Field(min_length=1)
    contract_schema: str = Field(min_length=1)
    output_schema_id: str = Field(min_length=1)
    shared_policy: str = Field(min_length=1)


class _SkillFrontmatter(_ManifestModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    metadata: _SkillMetadata


@dataclass(frozen=True, slots=True)
class LoadedRuntimeSkill:
    """Verified role instructions paired with their enforced contract."""

    contract: RuntimeSkillContract
    skill_name: str
    description: str
    role_instructions: str
    shared_policy: str
    skill_content_sha256: str
    shared_policy_content_sha256: str
    prompt_content_sha256: str

    @property
    def system_instructions(self) -> str:
        return f"{self.shared_policy}\n\n{self.role_instructions}"


@dataclass(frozen=True, slots=True)
class LoadedRuntimeSkillSet:
    """An all-or-nothing verified runtime-skill release."""

    manifest_sha256: str
    skills: tuple[LoadedRuntimeSkill, ...]

    def skill_for(self, role: RuntimeAgentRole) -> LoadedRuntimeSkill:
        for skill in self.skills:
            if skill.contract.agent_role is role:
                return skill
        raise RuntimeSkillLoadError("runtime skill is not available")


class RuntimeSkillLoader:
    """Load an exact-hash approved static skill bundle without partial success."""

    def __init__(
        self,
        manifest_path: Path,
        registry: RuntimeSkillRegistry = DEFAULT_RUNTIME_SKILLS,
    ) -> None:
        if not isinstance(manifest_path, Path):
            raise TypeError("runtime-skill manifest path must be a Path")
        if not isinstance(registry, RuntimeSkillRegistry):
            raise TypeError("runtime-skill registry must be validated")
        self._manifest_path = manifest_path
        self._registry = registry

    def load(self) -> LoadedRuntimeSkillSet:
        """Validate and return the complete bundle, or expose no skills."""
        try:
            return self._load()
        except RuntimeSkillLoadError:
            raise
        except (OSError, UnicodeError, yaml.YAMLError, ValidationError) as exc:
            raise RuntimeSkillLoadError(
                "runtime-skill bundle validation failed"
            ) from exc

    def _load(self) -> LoadedRuntimeSkillSet:
        manifest_path = self._manifest_path.resolve(strict=True)
        if not manifest_path.is_file():
            raise RuntimeSkillLoadError("runtime-skill manifest is not a file")
        manifest_bytes, manifest_text = _read_text(
            manifest_path,
            maximum_bytes=_MAX_MANIFEST_BYTES,
            label="manifest",
        )
        manifest = _RuntimeSkillManifest.model_validate(
            _load_yaml(manifest_text)
        )
        root = manifest_path.parent.resolve(strict=True)

        expected_roles = {role.value for role in RuntimeAgentRole}
        actual_roles = {entry.agent_role for entry in manifest.skills}
        if actual_roles != expected_roles:
            raise RuntimeSkillLoadError(
                "runtime-skill manifest must define every configured role"
            )

        shared_path = _resolve_owned_file(root, manifest.shared_policy.path)
        _, shared_text = _read_verified_text(
            shared_path,
            expected_sha256=manifest.shared_policy.content_sha256,
            maximum_bytes=_MAX_POLICY_BYTES,
            label="shared policy",
        )

        loaded: list[LoadedRuntimeSkill] = []
        for entry in manifest.skills:
            loaded.append(
                self._load_skill(
                    root=root,
                    shared_path=shared_path,
                    shared_text=shared_text,
                    shared_sha256=manifest.shared_policy.content_sha256,
                    entry=entry,
                )
            )

        loaded.sort(key=lambda item: item.contract.agent_role.value)
        return LoadedRuntimeSkillSet(
            manifest_sha256=sha256(manifest_bytes).hexdigest(),
            skills=tuple(loaded),
        )

    def _load_skill(
        self,
        *,
        root: Path,
        shared_path: Path,
        shared_text: str,
        shared_sha256: str,
        entry: _SkillEntry,
    ) -> LoadedRuntimeSkill:
        try:
            role = RuntimeAgentRole(entry.agent_role)
        except ValueError as exc:
            raise RuntimeSkillLoadError(
                "runtime-skill manifest contains an unknown role"
            ) from exc
        contract = self._registry.skill_for(role)
        _validate_entry_contract(entry, contract)

        skill_path = _resolve_owned_file(root, entry.path)
        _, skill_text = _read_verified_text(
            skill_path,
            expected_sha256=entry.content_sha256,
            maximum_bytes=_MAX_SKILL_BYTES,
            label="skill",
        )
        frontmatter_text, role_instructions = _split_skill(skill_text)
        frontmatter = _SkillFrontmatter.model_validate(
            _load_yaml(frontmatter_text)
        )
        _validate_frontmatter(
            frontmatter=frontmatter,
            entry=entry,
            skill_path=skill_path,
            root=root,
            shared_path=shared_path,
        )

        system_instructions = f"{shared_text}\n\n{role_instructions}"
        return LoadedRuntimeSkill(
            contract=contract,
            skill_name=frontmatter.name,
            description=frontmatter.description.strip(),
            role_instructions=role_instructions,
            shared_policy=shared_text,
            skill_content_sha256=entry.content_sha256,
            shared_policy_content_sha256=shared_sha256,
            prompt_content_sha256=sha256(
                system_instructions.encode("utf-8")
            ).hexdigest(),
        )


def _load_yaml(text: str) -> object:
    parsed = yaml.load(text, Loader=_StrictSafeLoader)
    if not isinstance(parsed, dict):
        raise RuntimeSkillLoadError("runtime-skill YAML must be a mapping")
    return parsed


def _resolve_owned_file(
    root: Path,
    relative_path: str,
    *,
    relative_to: Path | None = None,
) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise RuntimeSkillLoadError("runtime-skill paths must be relative")
    base = relative_to or root
    try:
        resolved = (base / candidate).resolve(strict=True)
    except OSError as exc:
        raise RuntimeSkillLoadError("runtime-skill file is unavailable") from exc
    if resolved == root or root not in resolved.parents:
        raise RuntimeSkillLoadError("runtime-skill path escapes its bundle")
    if not resolved.is_file():
        raise RuntimeSkillLoadError("runtime-skill path is not a file")
    return resolved


def _read_text(
    path: Path,
    *,
    maximum_bytes: int,
    label: str,
) -> tuple[bytes, str]:
    content = path.read_bytes()
    if not content or len(content) > maximum_bytes:
        raise RuntimeSkillLoadError(f"runtime-skill {label} size is invalid")
    text = content.decode("utf-8")
    if "\x00" in text or not text.strip():
        raise RuntimeSkillLoadError(f"runtime-skill {label} content is invalid")
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
    actual_sha256 = sha256(content).hexdigest()
    if not compare_digest(actual_sha256, expected_sha256):
        raise RuntimeSkillLoadError(f"runtime-skill {label} hash mismatch")
    return content, text


def _split_skill(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise RuntimeSkillLoadError("runtime skill has no frontmatter")
    try:
        closing_index = lines.index("---", 1)
    except ValueError as exc:
        raise RuntimeSkillLoadError(
            "runtime skill frontmatter is unterminated"
        ) from exc
    frontmatter = "\n".join(lines[1:closing_index])
    instructions = "\n".join(lines[closing_index + 1 :]).strip()
    if not frontmatter.strip() or not instructions:
        raise RuntimeSkillLoadError("runtime skill instructions are empty")
    return frontmatter, instructions


def _validate_entry_contract(
    entry: _SkillEntry,
    contract: RuntimeSkillContract,
) -> None:
    if (
        entry.skill_id != contract.skill_id
        or entry.version != contract.version
        or entry.contract_schema != contract.schema_version
        or entry.output_schema_id != contract.output_schema_id
    ):
        raise RuntimeSkillLoadError(
            "runtime-skill manifest does not match its Python contract"
        )


def _validate_frontmatter(
    *,
    frontmatter: _SkillFrontmatter,
    entry: _SkillEntry,
    skill_path: Path,
    root: Path,
    shared_path: Path,
) -> None:
    metadata = frontmatter.metadata
    if frontmatter.name != skill_path.parent.name:
        raise RuntimeSkillLoadError(
            "runtime-skill name does not match its directory"
        )
    if (
        metadata.runtime_skill_id != entry.skill_id
        or metadata.agent_role != entry.agent_role
        or metadata.version != entry.version
        or metadata.contract_schema != entry.contract_schema
        or metadata.output_schema_id != entry.output_schema_id
    ):
        raise RuntimeSkillLoadError(
            "runtime-skill frontmatter does not match its manifest"
        )
    referenced_shared_policy = _resolve_owned_file(
        root,
        metadata.shared_policy,
        relative_to=skill_path.parent,
    )
    if referenced_shared_policy != shared_path or root not in shared_path.parents:
        raise RuntimeSkillLoadError(
            "runtime skill references an unapproved shared policy"
        )
