"""Provider-neutral application service for browser-session commands."""

from collections.abc import Callable
from re import fullmatch

from app.fundamentals.session_provisioning import (
    ProviderSessionInspectionRequest,
    ProviderSessionLifecycle,
    ProviderSessionProvisioner,
    ProviderSessionProvisioningRequest,
    ProviderSessionRequest,
    ProviderSessionRevocationRequest,
    validate_provider_session_response_binding,
)


class ProviderSessionCommandError(Exception):
    """Sanitized failure at the provider-session application boundary."""

    def __init__(self) -> None:
        super().__init__("Provider session command could not be completed safely")


SessionOperation = Callable[..., ProviderSessionLifecycle]


class ProviderSessionService:
    """Execute lifecycle commands without exposing provider implementation details."""

    def __init__(self, provisioner: ProviderSessionProvisioner) -> None:
        if not isinstance(provisioner, ProviderSessionProvisioner):
            raise TypeError("Provider session service requires a provisioner")
        fingerprint = provisioner.configuration_fingerprint
        if (
            not isinstance(fingerprint, str)
            or fullmatch(r"[a-f0-9]{64}", fingerprint) is None
        ):
            raise TypeError("Provider session provisioner fingerprint is invalid")
        self._provisioner = provisioner
        self._configuration_fingerprint = fingerprint

    @property
    def configuration_fingerprint(self) -> str:
        return self._configuration_fingerprint

    def status(
        self,
        *,
        request: ProviderSessionInspectionRequest,
    ) -> ProviderSessionLifecycle:
        return self._execute(
            request=request,
            expected_type=ProviderSessionInspectionRequest,
            operation=self._provisioner.inspect,
        )

    def provision(
        self,
        *,
        request: ProviderSessionProvisioningRequest,
    ) -> ProviderSessionLifecycle:
        return self._execute(
            request=request,
            expected_type=ProviderSessionProvisioningRequest,
            operation=self._provisioner.provision,
        )

    def revoke(
        self,
        *,
        request: ProviderSessionRevocationRequest,
    ) -> ProviderSessionLifecycle:
        return self._execute(
            request=request,
            expected_type=ProviderSessionRevocationRequest,
            operation=self._provisioner.revoke,
        )

    @staticmethod
    def _execute(
        *,
        request: ProviderSessionRequest,
        expected_type: type[ProviderSessionRequest],
        operation: SessionOperation,
    ) -> ProviderSessionLifecycle:
        if not isinstance(request, expected_type):
            raise TypeError("Provider session command received the wrong request type")
        try:
            lifecycle = operation(request=request)
            validate_provider_session_response_binding(request, lifecycle)
        except Exception as exc:
            raise ProviderSessionCommandError() from exc
        return lifecycle
