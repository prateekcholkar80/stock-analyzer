"""Composition root for provider-neutral fundamental evidence gateways."""

from collections.abc import Callable, Mapping
from datetime import datetime
import os
from pathlib import Path

from pydantic import ValidationError

from app.exceptions import ConfigurationError
from app.fundamentals.adapters.tijori_mcp import TijoriMcpAdapter
from app.fundamentals.tijori_mcp_contracts import (
    TijoriMcpAdapterSettings,
    TijoriMcpTransport,
)
from app.fundamentals.transports.stdio_mcp import (
    TijoriStdioMcpSettings,
    TijoriStdioMcpTransport,
)
from app.gateways.fundamentals import FundamentalEvidenceGateway


TijoriTransportBuilder = Callable[
    [TijoriStdioMcpSettings],
    TijoriMcpTransport,
]

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
