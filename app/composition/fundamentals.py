"""Composition root for provider-neutral fundamental evidence gateways."""

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
import os
from pathlib import Path
import stat

from pydantic import ValidationError

from app.exceptions import ConfigurationError
from app.fundamentals.adapters.tijori_mcp import TijoriMcpAdapter
from app.fundamentals.local_session_provisioner import (
    LocalFileProviderSessionProvisioner,
    LocalSessionProvisioningError,
)
from app.fundamentals.session_provisioning import ProviderSessionProvisioner
from app.fundamentals.tijori_mcp_contracts import (
    TijoriMcpAdapterSettings,
    TijoriMcpTransport,
    TijoriMcpTransportError,
)
from app.fundamentals.transports.stdio_mcp import (
    TijoriStdioMcpSettings,
    TijoriStdioMcpTransport,
)
from app.gateways.fundamentals import FundamentalEvidenceGateway
from app.models.fundamental_storage import FUNDAMENTAL_MAX_RETENTION
from app.services.fundamental_evidence import FundamentalEvidenceCoordinator
from app.services.provider_sessions import ProviderSessionService
from app.storage.fundamental_repositories import (
    FundamentalSnapshotRepository,
)
from app.storage.financial_document_repositories import (
    StructuredFinancialDocumentRepository,
)
from app.storage.peer_comparison_repositories import PeerComparisonRepository
from app.storage.benchmarking_financials_repositories import (
    BenchmarkingFinancialsRepository,
)


TijoriTransportBuilder = Callable[
    [TijoriStdioMcpSettings],
    TijoriMcpTransport,
]
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_PROVISIONING_CLI = Path(
    "integrations/tijori-mcp/src/provision-session-cli.js"
)


def load_tijori_stdio_settings(
    environment: Mapping[str, str] | None = None,
) -> TijoriStdioMcpSettings:
    """Load non-secret pinned runtime settings from an explicit environment."""

    source = environment if environment is not None else os.environ
    required = {
        "JARVIS_TIJORI_RUNTIME_EXECUTABLE": "runtime_executable",
        "JARVIS_TIJORI_RUNTIME_SHA256": "runtime_sha256",
        "JARVIS_TIJORI_SERVER_ENTRYPOINT": "server_entrypoint",
        "JARVIS_TIJORI_SERVER_SHA256": "server_sha256",
        "JARVIS_TIJORI_SESSION_ROOT": "session_root",
    }
    values: dict[str, object] = {}
    try:
        for environment_name, field_name in required.items():
            value = source.get(environment_name)
            if value is None or not value.strip():
                raise ValueError("required setting is missing")
            values[field_name] = (
                Path(value.strip())
                if field_name.endswith(("executable", "entrypoint", "root"))
                else value.strip()
            )

        optional_text = {
            "JARVIS_TIJORI_PROTOCOL_VERSION": "protocol_version",
            "JARVIS_TIJORI_PROVIDER_CONTRACT_VERSION": (
                "provider_contract_version"
            ),
        }
        optional_float = {
            "JARVIS_TIJORI_TIMEOUT_SECONDS": "timeout_seconds",
            "JARVIS_TIJORI_TERMINATE_GRACE_SECONDS": (
                "terminate_grace_seconds"
            ),
        }
        optional_integer = {
            "JARVIS_TIJORI_MAX_MESSAGE_BYTES": "max_message_bytes",
            "JARVIS_TIJORI_MAX_CONCURRENCY": "max_concurrency",
            "JARVIS_TIJORI_MAX_SESSION_BYTES": "max_session_bytes",
        }
        for environment_name, field_name in optional_text.items():
            value = source.get(environment_name)
            if value is not None and value.strip():
                values[field_name] = value.strip()
        for environment_name, field_name in optional_float.items():
            value = source.get(environment_name)
            if value is not None and value.strip():
                values[field_name] = float(value.strip())
        for environment_name, field_name in optional_integer.items():
            value = source.get(environment_name)
            if value is not None and value.strip():
                values[field_name] = int(value.strip())
        return TijoriStdioMcpSettings.model_validate(values)
    except (TypeError, ValueError, ValidationError) as exc:
        raise ConfigurationError(
            "Tijori local runtime configuration is missing or invalid"
        ) from exc


def compose_tijori_fundamental_gateway(
    *,
    transport_settings: TijoriStdioMcpSettings,
    adapter_settings: TijoriMcpAdapterSettings | None = None,
    clock: Callable[[], datetime] | None = None,
    transport_builder: TijoriTransportBuilder | None = None,
) -> FundamentalEvidenceGateway:
    """Compose Tijori behind the provider-neutral read-only gateway.

    Composition validates pinned runtime paths through the transport
    constructor but does not start a process, browser, session, or provider
    request. Session identity remains supplied later through each
    ``ProviderConnectionScope``.
    """

    if not isinstance(transport_settings, TijoriStdioMcpSettings):
        raise TypeError("Tijori composition requires stdio transport settings")

    if transport_builder is None:
        transport: TijoriMcpTransport = TijoriStdioMcpTransport(
            settings=transport_settings,
            clock=clock,
        )
    else:
        transport = transport_builder(transport_settings)

    effective_adapter_settings = adapter_settings or TijoriMcpAdapterSettings(
        provider_contract_version=transport_settings.provider_contract_version
    )
    if (
        effective_adapter_settings.provider_contract_version
        != transport_settings.provider_contract_version
    ):
        raise ConfigurationError(
            "Tijori adapter and transport contract versions do not match"
        )

    return TijoriMcpAdapter(
        transport=transport,
        settings=effective_adapter_settings,
        clock=clock,
    )


def compose_tijori_fundamental_coordinator(
    *,
    transport_settings: TijoriStdioMcpSettings,
    repository: FundamentalSnapshotRepository,
    adapter_settings: TijoriMcpAdapterSettings | None = None,
    clock: Callable[[], datetime] | None = None,
    retention: timedelta = FUNDAMENTAL_MAX_RETENTION,
    transport_builder: TijoriTransportBuilder | None = None,
) -> FundamentalEvidenceCoordinator:
    """Compose cache-aware Tijori evidence using an existing repository.

    The caller owns the repository lifecycle. In the browser runtime this is
    the same ``DuckDBJarvisStorage`` instance already used for research data;
    composition neither opens another database nor performs provider I/O.
    """

    if not isinstance(repository, FundamentalSnapshotRepository):
        raise TypeError(
            "Tijori coordinator composition requires a fundamental repository"
        )
    if not isinstance(repository, StructuredFinancialDocumentRepository):
        raise TypeError(
            "Tijori coordinator composition requires structured document "
            "storage"
        )
    if not isinstance(repository, PeerComparisonRepository):
        raise TypeError(
            "Tijori coordinator composition requires Peer Comparison storage"
        )
    if not isinstance(repository, BenchmarkingFinancialsRepository):
        raise TypeError(
            "Tijori coordinator composition requires Benchmarking Financials "
            "storage"
        )
    gateway = compose_tijori_fundamental_gateway(
        transport_settings=transport_settings,
        adapter_settings=adapter_settings,
        clock=clock,
        transport_builder=transport_builder,
    )
    return FundamentalEvidenceCoordinator(
        gateway=gateway,
        repository=repository,
        structured_document_repository=repository,
        peer_comparison_repository=repository,
        benchmarking_financials_repository=repository,
        clock=clock,
        retention=retention,
    )


def compose_tijori_session_provisioner(
    *,
    transport_settings: TijoriStdioMcpSettings,
    session_max_age: timedelta = timedelta(hours=12),
    interactive_timeout: timedelta = timedelta(minutes=5),
    repository_root: Path | None = None,
    clock: Callable[[], datetime] | None = None,
    process_runner: Callable[..., object] | None = None,
) -> ProviderSessionProvisioner:
    """Compose explicit user-driven session provisioning without provider I/O.

    The read-only transport supplies the same pinned Node runtime and scoped
    session-path algorithm used for MCP calls. The interactive CLI is resolved
    only from its fixed location under the trusted repository root.
    """

    if not isinstance(transport_settings, TijoriStdioMcpSettings):
        raise TypeError("Tijori session composition requires transport settings")
    root = repository_root or _REPOSITORY_ROOT
    if not isinstance(root, Path) or not root.is_absolute():
        raise ConfigurationError(
            "Tijori session provisioning configuration is invalid"
        )
    try:
        canonical_root = root.resolve(strict=True)
        if canonical_root != root:
            raise ValueError("repository root must be canonical")
        cli_path = canonical_root / _PROVISIONING_CLI
        cli_info = cli_path.lstat()
        if (
            stat.S_ISLNK(cli_info.st_mode)
            or not stat.S_ISREG(cli_info.st_mode)
            or cli_info.st_uid != os.getuid()
            or cli_info.st_mode & 0o022
        ):
            raise ValueError("provisioning CLI is not trusted")

        transport = TijoriStdioMcpTransport(
            settings=transport_settings,
            clock=clock,
        )
        return LocalFileProviderSessionProvisioner(
            session_root=transport_settings.session_root,
            session_path_resolver=transport.session_path_for,
            session_max_age=session_max_age,
            max_session_bytes=transport_settings.max_session_bytes,
            clock=clock,
            interactive_command=(
                transport_settings.runtime_executable,
                cli_path,
            ),
            interactive_timeout=interactive_timeout,
            process_runner=process_runner,
        )
    except (
        LocalSessionProvisioningError,
        OSError,
        TijoriMcpTransportError,
        TypeError,
        ValueError,
    ) as exc:
        raise ConfigurationError(
            "Tijori session provisioning configuration is invalid"
        ) from exc


def compose_tijori_session_service(
    *,
    transport_settings: TijoriStdioMcpSettings,
    session_max_age: timedelta = timedelta(hours=12),
    interactive_timeout: timedelta = timedelta(minutes=5),
    repository_root: Path | None = None,
    clock: Callable[[], datetime] | None = None,
    process_runner: Callable[..., object] | None = None,
) -> ProviderSessionService:
    """Compose the provider-neutral command service for API injection."""

    provisioner = compose_tijori_session_provisioner(
        transport_settings=transport_settings,
        session_max_age=session_max_age,
        interactive_timeout=interactive_timeout,
        repository_root=repository_root,
        clock=clock,
        process_runner=process_runner,
    )
    return ProviderSessionService(provisioner)
