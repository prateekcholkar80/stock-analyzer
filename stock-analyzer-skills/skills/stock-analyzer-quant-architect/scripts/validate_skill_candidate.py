#!/usr/bin/env python3
"""Validate a Jarvis skill-change candidate without mutating any artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker


_BUNDLE_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_SCHEMA = (
    _BUNDLE_ROOT / "references" / "skill-change-candidate.schema.json"
)
_SEMVER_PATTERN = re.compile(
    r"^(?P<major>0|[1-9][0-9]*)\."
    r"(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?$"
)
_PRE_PROMOTION_STATES = {
    "DRAFT",
    "READY_FOR_EVALUATION",
    "EVALUATING",
    "REJECTED",
}
_APPROVED_STATES = {"APPROVED", "PROMOTED", "ROLLED_BACK"}


class CandidateValidationError(ValueError):
    """A sanitized collection of candidate validation failures."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = tuple(errors)
        super().__init__("; ".join(self.errors))


@dataclass(frozen=True, slots=True)
class ValidatedSkillCandidate:
    candidate_id: str
    status: str
    target_skill_path: Path
    record_sha256: str


def sha256_file(path: Path) -> str:
    """Return the SHA-256 fingerprint of an artifact's exact bytes."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_record_sha256(record: Mapping[str, Any]) -> str:
    """Fingerprint a candidate using stable UTF-8 canonical JSON."""

    canonical = json.dumps(
        record,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def load_candidate(path: Path) -> Mapping[str, Any]:
    """Load one JSON object without exposing its contents in errors."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CandidateValidationError(
            (f"candidate JSON is invalid at line {exc.lineno}, column {exc.colno}",)
        ) from None
    except OSError as exc:
        raise CandidateValidationError(
            (f"candidate file could not be read ({exc.__class__.__name__})",)
        ) from None
    if not isinstance(payload, dict):
        raise CandidateValidationError(("candidate root must be a JSON object",))
    return payload


def validate_candidate(
    record: Mapping[str, Any],
    *,
    bundle_root: Path = _BUNDLE_ROOT,
    schema_path: Path = _DEFAULT_SCHEMA,
) -> ValidatedSkillCandidate:
    """Validate schema, lifecycle, and active-target identity without writes."""

    schema = _load_schema(schema_path)
    schema_errors = sorted(
        Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        ).iter_errors(record),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if schema_errors:
        raise CandidateValidationError(
            tuple(_safe_schema_error(error) for error in schema_errors)
        )

    errors: list[str] = []
    target = record["target"]
    target_path = _resolve_target(bundle_root, target["skill_path"], errors)
    if target_path is not None:
        _validate_active_target(target_path, target, errors)
    _validate_version_progression(target, errors)
    _validate_lifecycle(record, errors)

    if errors:
        raise CandidateValidationError(errors)

    assert target_path is not None
    return ValidatedSkillCandidate(
        candidate_id=record["candidate_id"],
        status=record["status"],
        target_skill_path=target_path,
        record_sha256=canonical_record_sha256(record),
    )


def _load_schema(path: Path) -> Mapping[str, Any]:
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (json.JSONDecodeError, OSError) as exc:
        raise CandidateValidationError(
            (f"candidate schema could not be loaded ({exc.__class__.__name__})",)
        ) from None
    except Exception as exc:
        raise CandidateValidationError(
            (f"candidate schema is invalid ({exc.__class__.__name__})",)
        ) from None
    if not isinstance(schema, dict):
        raise CandidateValidationError(("candidate schema root must be an object",))
    return schema


def _safe_schema_error(error: Any) -> str:
    location = "$"
    for part in error.absolute_path:
        if isinstance(part, int):
            location += f"[{part}]"
        else:
            location += f".{part}"
    return f"{location}: violates the {error.validator!r} schema constraint"


def _resolve_target(
    bundle_root: Path,
    skill_path: str,
    errors: list[str],
) -> Path | None:
    root = bundle_root.resolve()
    candidate = (root / skill_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        errors.append("target skill path escapes the configured bundle root")
        return None
    if not candidate.is_file():
        errors.append("target skill file does not exist")
        return None
    return candidate


def _validate_active_target(
    target_path: Path,
    target: Mapping[str, Any],
    errors: list[str],
) -> None:
    try:
        name, version = _read_skill_identity(target_path)
    except (OSError, ValueError):
        errors.append("target skill frontmatter could not be read safely")
        return
    if name != target["skill_name"]:
        errors.append("target skill name does not match the active artifact")
    if version != target["base_version"]:
        errors.append("base version does not match the active skill")
    if sha256_file(target_path) != target["base_content_sha256"]:
        errors.append("base content hash does not match the active skill")


def _read_skill_identity(path: Path) -> tuple[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        raise ValueError("missing frontmatter")

    name: str | None = None
    version: str | None = None
    in_metadata = False
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line.startswith("name:"):
            name = _unquote_scalar(line.partition(":")[2].strip())
            continue
        if line == "metadata:":
            in_metadata = True
            continue
        if in_metadata and line.startswith("  version:"):
            version = _unquote_scalar(line.partition(":")[2].strip())
            continue
        if line and not line.startswith(" "):
            in_metadata = False
    if not name or not version:
        raise ValueError("missing skill identity")
    return name, version


def _unquote_scalar(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _validate_version_progression(
    target: Mapping[str, Any],
    errors: list[str],
) -> None:
    base = _semver_key(target["base_version"])
    candidate = _semver_key(target["candidate_version"])
    if candidate <= base:
        errors.append("candidate version must be greater than the base version")


def _semver_key(value: str) -> tuple[Any, ...]:
    match = _SEMVER_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError("schema must validate semantic versions first")
    prerelease = match.group("prerelease")
    prerelease_key: tuple[tuple[int, Any], ...] = ()
    release_rank = 1
    if prerelease is not None:
        release_rank = 0
        prerelease_key = tuple(
            (0, int(part)) if part.isdigit() else (1, part)
            for part in prerelease.split(".")
        )
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
        release_rank,
        prerelease_key,
    )


def _validate_lifecycle(record: Mapping[str, Any], errors: list[str]) -> None:
    status = record["status"]
    approval = record["approval"]
    promotion = record.get("promotion")
    rollback = record["rollback"]
    results = record["evaluation_results"]

    if rollback["target_version"] != record["target"]["base_version"]:
        errors.append("rollback target must equal the candidate base version")

    if status in _PRE_PROMOTION_STATES and promotion is not None:
        errors.append("a pre-promotion candidate cannot contain promotion metadata")
    if status in {"DRAFT", "READY_FOR_EVALUATION", "EVALUATING"}:
        if approval["status"] != "PENDING":
            errors.append("an unevaluated candidate approval must remain pending")
    if status == "REJECTED" and approval["status"] != "REJECTED":
        errors.append("a rejected candidate requires a rejected approval state")

    if status in _APPROVED_STATES:
        if not results or any(result["status"] != "PASSED" for result in results):
            errors.append("an approved candidate requires only passing evaluations")
        if approval["status"] != "APPROVED":
            errors.append("an approved lifecycle state requires human approval")
        if (
            approval["approved_candidate_sha256"]
            != record["target"]["candidate_content_sha256"]
        ):
            errors.append("approval hash does not match the candidate skill hash")

    if status in {"PROMOTED", "ROLLED_BACK"} and promotion is None:
        errors.append("a promoted lifecycle state requires promotion metadata")
    if status not in {"PROMOTED", "ROLLED_BACK"} and promotion is not None:
        errors.append("promotion metadata is allowed only after promotion")

    if status == "ROLLED_BACK":
        if rollback["status"] != "EXECUTED":
            errors.append("a rolled-back candidate requires executed rollback metadata")
        if not rollback.get("executed_at") or not rollback.get("executed_by"):
            errors.append("executed rollback requires time and human identity")
    elif rollback["status"] != "READY":
        errors.append("rollback must remain ready until it is executed")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate one skill-change candidate without modifying it.",
    )
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--bundle-root", type=Path, default=_BUNDLE_ROOT)
    parser.add_argument("--schema", type=Path, default=_DEFAULT_SCHEMA)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        record = load_candidate(args.candidate)
        result = validate_candidate(
            record,
            bundle_root=args.bundle_root,
            schema_path=args.schema,
        )
    except CandidateValidationError as exc:
        print("INVALID", file=sys.stderr)
        for error in exc.errors:
            print(f"- {error}", file=sys.stderr)
        return 2

    print(
        "VALID "
        f"candidate_id={result.candidate_id} "
        f"status={result.status} "
        f"record_sha256={result.record_sha256}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
