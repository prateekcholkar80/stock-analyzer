"""Tenant-owned provider-connection registry and browser scope resolver."""

from re import fullmatch
from threading import RLock
from typing import Protocol, runtime_checkable

from app.models.fundamentals import ProviderConnectionScope


_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
_FINGERPRINT_PATTERN = r"^[a-f0-9]{64}$"


class ProviderConnectionResolutionError(Exception):
    """Non-disclosing failure to resolve an owned provider connection."""

    def __init__(self) -> None:
        super().__init__("Provider connection could not be resolved safely")


@runtime_checkable
class ProviderConnectionTarget(Protocol):
    """Structural target accepted from an outer transport such as HTTP."""

    @property
    def provider_connection_id(self) -> str:
        ...

    @property
    def provider(self) -> str:
        ...

    @property
    def account_reference_hash(self) -> str | None:
        ...


@runtime_checkable
class ProviderConnectionScopeResolver(Protocol):
    def __call__(
        self,
        browser_session_id: str,
        target: ProviderConnectionTarget,
    ) -> ProviderConnectionScope:
        ...


class InMemoryProviderConnectionRegistry(ProviderConnectionScopeResolver):
    """Thread-safe runtime registry with strict tenant and session isolation."""

    def __init__(self) -> None:
        self._connections: dict[tuple[str, str], ProviderConnectionScope] = {}
        self._tenant_by_browser_session: dict[str, str] = {}
        self._lock = RLock()

    def register_connection(
        self,
        connection: ProviderConnectionScope,
    ) -> ProviderConnectionScope:
        if not isinstance(connection, ProviderConnectionScope):
            raise TypeError("Provider registry requires a connection scope")
        key = (connection.tenant_id, connection.provider_connection_id)
        with self._lock:
            existing = self._connections.get(key)
            if existing is not None and existing != connection:
                raise ProviderConnectionResolutionError()
            self._connections[key] = connection
        return connection

    def remove_connection(
        self,
        *,
        tenant_id: str,
        provider_connection_id: str,
    ) -> bool:
        self._validate_identifier(tenant_id)
        self._validate_identifier(provider_connection_id)
        with self._lock:
            return self._connections.pop(
                (tenant_id, provider_connection_id),
                None,
            ) is not None

    def bind_browser_session(
        self,
        *,
        browser_session_id: str,
        tenant_id: str,
    ) -> None:
        self._validate_identifier(browser_session_id)
        self._validate_identifier(tenant_id)
        with self._lock:
            existing = self._tenant_by_browser_session.get(browser_session_id)
            if existing is not None and existing != tenant_id:
                raise ProviderConnectionResolutionError()
            self._tenant_by_browser_session[browser_session_id] = tenant_id

    def unbind_browser_session(self, browser_session_id: str) -> bool:
        self._validate_identifier(browser_session_id)
        with self._lock:
            return self._tenant_by_browser_session.pop(
                browser_session_id,
                None,
            ) is not None

    def resolve(
        self,
        browser_session_id: str,
        target: ProviderConnectionTarget,
    ) -> ProviderConnectionScope:
        self._validate_identifier(browser_session_id)
        provider_connection_id, provider, account_hash = (
            self._validated_target_values(target)
        )
        with self._lock:
            tenant_id = self._tenant_by_browser_session.get(browser_session_id)
            if tenant_id is None:
                raise ProviderConnectionResolutionError()
            connection = self._connections.get(
                (tenant_id, provider_connection_id)
            )
            if (
                connection is None
                or connection.provider != provider
                or connection.account_reference_hash != account_hash
            ):
                raise ProviderConnectionResolutionError()
            return connection

    def __call__(
        self,
        browser_session_id: str,
        target: ProviderConnectionTarget,
    ) -> ProviderConnectionScope:
        return self.resolve(browser_session_id, target)

    @staticmethod
    def _validate_identifier(value: object) -> None:
        if (
            not isinstance(value, str)
            or len(value) > 128
            or fullmatch(_ID_PATTERN, value) is None
        ):
            raise ProviderConnectionResolutionError()

    @classmethod
    def _validated_target_values(
        cls,
        target: object,
    ) -> tuple[str, str, str | None]:
        try:
            if not isinstance(target, ProviderConnectionTarget):
                raise ProviderConnectionResolutionError()
            provider_connection_id = target.provider_connection_id
            provider = target.provider
            account_hash = target.account_reference_hash
            cls._validate_identifier(provider_connection_id)
            cls._validate_identifier(provider)
            if account_hash is not None and (
                not isinstance(account_hash, str)
                or fullmatch(_FINGERPRINT_PATTERN, account_hash) is None
            ):
                raise ProviderConnectionResolutionError()
        except ProviderConnectionResolutionError:
            raise
        except Exception as exc:
            raise ProviderConnectionResolutionError() from exc
        return provider_connection_id, provider, account_hash
