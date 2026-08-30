"""Provider-neutral contracts for user-owned browser-session lifecycles."""

from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Literal, Protocol, Self, runtime_checkable

from pydantic import Field, computed_field, field_validator, model_validator

from app.models.fundamentals import FundamentalModel, ProviderConnectionScope


_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
_FINGERPRINT_PATTERN = r"^[a-f0-9]{64}$"


class ProviderSessionStatus(StrEnum):
    UNCONFIGURED = "unconfigured"
    READY = "ready"
    EXPIRED = "expired"
    REVOKED = "revoked"


class ProviderSessionRevocationReason(StrEnum):
    USER_REQUESTED = "user_requested"
    CONNECTION_REMOVED = "connection_removed"
    SECURITY_ROTATION = "security_rotation"
    EXPIRED = "expired"


class ProviderSessionInspectionRequest(FundamentalModel):
    schema_version: Literal["jarvis.provider_session_inspection_request.v1"] = (
        "jarvis.provider_session_inspection_request.v1"
    )
    request_id: str = Field(min_length=1, max_length=160, pattern=_ID_PATTERN)
    connection: ProviderConnectionScope
    requested_at: datetime

    @field_validator("requested_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        return _aware(value, "provider session request time")


class ProviderSessionProvisioningRequest(FundamentalModel):
    schema_version: Literal[
        "jarvis.provider_session_provisioning_request.v1"
    ] = "jarvis.provider_session_provisioning_request.v1"
    request_id: str = Field(min_length=1, max_length=160, pattern=_ID_PATTERN)
    connection: ProviderConnectionScope
    requested_at: datetime
    user_interaction_authorized: bool
    replace_existing: bool = False

    @field_validator("requested_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        return _aware(value, "provider session provisioning time")

    @model_validator(mode="after")
    def require_explicit_user_authorization(self) -> Self:
        if not self.user_interaction_authorized:
            raise ValueError(
                "provider session provisioning requires explicit user authorization"
            )
        return self


class ProviderSessionRevocationRequest(FundamentalModel):
    schema_version: Literal[
        "jarvis.provider_session_revocation_request.v1"
    ] = "jarvis.provider_session_revocation_request.v1"
    request_id: str = Field(min_length=1, max_length=160, pattern=_ID_PATTERN)
    connection: ProviderConnectionScope
    requested_at: datetime
    reason: ProviderSessionRevocationReason
    secure_delete_required: bool = True

    @field_validator("requested_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        return _aware(value, "provider session revocation time")

    @model_validator(mode="after")
    def require_secure_deletion(self) -> Self:
        if not self.secure_delete_required:
            raise ValueError("provider session revocation requires secure deletion")
        return self


class ProviderSessionLifecycle(FundamentalModel):
    """Secret-free lifecycle metadata; never contains a path or session data."""

    schema_version: Literal["jarvis.provider_session_lifecycle.v1"] = (
        "jarvis.provider_session_lifecycle.v1"
    )
    connection: ProviderConnectionScope
    status: ProviderSessionStatus
    checked_at: datetime
    session_reference_hash: str | None = Field(
        default=None,
        pattern=_FINGERPRINT_PATTERN,
    )
    provisioned_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    revocation_reason: ProviderSessionRevocationReason | None = None

    @field_validator(
        "checked_at",
        "provisioned_at",
        "expires_at",
        "revoked_at",
    )
    @classmethod
    def require_lifecycle_timezone(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        return None if value is None else _aware(value, "session lifecycle time")

    @model_validator(mode="after")
    def validate_status_shape(self) -> Self:
        if self.status is ProviderSessionStatus.UNCONFIGURED:
            if any(
                value is not None
                for value in (
                    self.session_reference_hash,
                    self.provisioned_at,
                    self.expires_at,
                    self.revoked_at,
                    self.revocation_reason,
                )
            ):
                raise ValueError("unconfigured session cannot contain lifecycle data")
            return self

        if self.session_reference_hash is None or self.provisioned_at is None:
            raise ValueError("configured session requires an opaque reference and time")
        if self.checked_at < self.provisioned_at:
            raise ValueError("session check cannot predate provisioning")

        if self.status is ProviderSessionStatus.READY:
            if self.revoked_at is not None or self.revocation_reason is not None:
                raise ValueError("ready session cannot contain revocation state")
            if self.expires_at is not None and self.checked_at >= self.expires_at:
                raise ValueError("expired session cannot remain ready")
        elif self.status is ProviderSessionStatus.EXPIRED:
            if (
                self.expires_at is None
                or self.checked_at < self.expires_at
                or self.revoked_at is not None
                or self.revocation_reason is not None
            ):
                raise ValueError("expired session requires a reached expiry only")
        elif self.status is ProviderSessionStatus.REVOKED:
            if self.revoked_at is None or self.revocation_reason is None:
                raise ValueError("revoked session requires time and reason")
            if (
                self.revoked_at < self.provisioned_at
                or self.checked_at < self.revoked_at
            ):
                raise ValueError("session revocation timestamps are inconsistent")
        return self

    @computed_field
    @property
    def lifecycle_fingerprint(self) -> str:
        payload = self.model_dump_json(exclude={"lifecycle_fingerprint"})
        return sha256(payload.encode("utf-8")).hexdigest()


ProviderSessionRequest = (
    ProviderSessionInspectionRequest
    | ProviderSessionProvisioningRequest
    | ProviderSessionRevocationRequest
)


def validate_provider_session_response_binding(
    request: ProviderSessionRequest,
    lifecycle: ProviderSessionLifecycle,
) -> None:
    if not isinstance(
        request,
        (
            ProviderSessionInspectionRequest,
            ProviderSessionProvisioningRequest,
            ProviderSessionRevocationRequest,
        ),
    ) or not isinstance(lifecycle, ProviderSessionLifecycle):
        raise TypeError("provider session binding requires known contracts")
    if lifecycle.connection != request.connection:
        raise ValueError("provider session response crossed its connection scope")
    if lifecycle.checked_at < request.requested_at:
        raise ValueError("provider session response predates its request")
    if (
        isinstance(request, ProviderSessionProvisioningRequest)
        and lifecycle.status is not ProviderSessionStatus.READY
    ):
        raise ValueError("provisioning must return a ready session")
    if (
        isinstance(request, ProviderSessionRevocationRequest)
        and lifecycle.status is not ProviderSessionStatus.REVOKED
    ):
        raise ValueError("revocation must return a revoked session")


@runtime_checkable
class ProviderSessionProvisioner(Protocol):
    """Interactive session port; implementations must not accept credentials."""

    @property
    def configuration_fingerprint(self) -> str:
        ...

    def inspect(
        self,
        *,
        request: ProviderSessionInspectionRequest,
    ) -> ProviderSessionLifecycle:
        ...

    def provision(
        self,
        *,
        request: ProviderSessionProvisioningRequest,
    ) -> ProviderSessionLifecycle:
        ...

    def revoke(
        self,
        *,
        request: ProviderSessionRevocationRequest,
    ) -> ProviderSessionLifecycle:
        ...


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include timezone information")
    return value
