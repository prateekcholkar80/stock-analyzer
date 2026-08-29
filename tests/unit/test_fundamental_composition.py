import hashlib
from datetime import UTC, datetime
from pathlib import Path
import tempfile
import unittest

from app.composition.fundamentals import (
    compose_tijori_fundamental_gateway,
    load_tijori_stdio_settings,
)
from app.exceptions import ConfigurationError
from app.fundamentals.adapters.tijori_mcp import TijoriMcpAdapter
from app.fundamentals.tijori_mcp_contracts import (
    TijoriMcpAdapterSettings,
    TijoriMcpToolResult,
    TijoriMcpTransportInspection,
)
from app.fundamentals.transports.stdio_mcp import TijoriStdioMcpSettings
from app.gateways.fundamentals import FundamentalEvidenceGateway
from app.models.fundamentals import ProviderConnectionScope


NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FakeTransport:
    configuration_fingerprint = "a" * 64

    def inspect(
        self,
        *,
        connection: ProviderConnectionScope,
    ) -> TijoriMcpTransportInspection:
        raise AssertionError("Composition must not inspect a provider session")

    def call_tool(
        self,
        *,
        connection: ProviderConnectionScope,
        tool_name: str,
        arguments: dict[str, object],
    ) -> TijoriMcpToolResult:
        raise AssertionError("Composition must not invoke a provider tool")


class TijoriFundamentalCompositionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        root.chmod(0o700)
        runtime = root / "node-24"
        runtime.write_text("synthetic runtime", encoding="utf-8")
        runtime.chmod(0o700)
        server = root / "index.js"
        server.write_text("// synthetic server", encoding="utf-8")
        server.chmod(0o600)
        self.settings = TijoriStdioMcpSettings(
            runtime_executable=runtime,
            runtime_sha256=file_hash(runtime),
            server_entrypoint=server,
            server_sha256=file_hash(server),
            session_root=root,
            provider_contract_version="tijori.local_contract.v1",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_composes_provider_neutral_gateway_without_provider_io(self):
        received = []

        def builder(settings):
            received.append(settings)
            return FakeTransport()

        gateway = compose_tijori_fundamental_gateway(
            transport_settings=self.settings,
            adapter_settings=TijoriMcpAdapterSettings(
                provider_contract_version="tijori.local_contract.v1"
            ),
            clock=lambda: NOW,
            transport_builder=builder,
        )

        self.assertIsInstance(gateway, FundamentalEvidenceGateway)
        self.assertIsInstance(gateway, TijoriMcpAdapter)
        self.assertEqual(received, [self.settings])
        self.assertEqual(len(gateway.configuration_fingerprint), 64)

    def test_default_composition_constructs_transport_without_starting_it(self):
        gateway = compose_tijori_fundamental_gateway(
            transport_settings=self.settings,
            clock=lambda: NOW,
        )

        self.assertIsInstance(gateway, FundamentalEvidenceGateway)
        self.assertEqual(len(gateway.configuration_fingerprint), 64)

    def test_rejects_invalid_settings_and_incompatible_transport(self):
        with self.assertRaisesRegex(TypeError, "stdio transport settings"):
            compose_tijori_fundamental_gateway(transport_settings=None)

        with self.assertRaisesRegex(TypeError, "compatible transport"):
            compose_tijori_fundamental_gateway(
                transport_settings=self.settings,
                transport_builder=lambda settings: object(),
            )

    def test_loads_only_non_secret_pinned_runtime_settings(self):
        environment = {
            "JARVIS_TIJORI_RUNTIME_EXECUTABLE": str(
                self.settings.runtime_executable
            ),
            "JARVIS_TIJORI_RUNTIME_SHA256": self.settings.runtime_sha256,
            "JARVIS_TIJORI_SERVER_ENTRYPOINT": str(
                self.settings.server_entrypoint
            ),
            "JARVIS_TIJORI_SERVER_SHA256": self.settings.server_sha256,
            "JARVIS_TIJORI_SESSION_ROOT": str(self.settings.session_root),
            "JARVIS_TIJORI_PROVIDER_CONTRACT_VERSION": (
                "tijori.local_contract.v1"
            ),
            "JARVIS_TIJORI_TIMEOUT_SECONDS": "20",
            "JARVIS_TIJORI_MAX_CONCURRENCY": "2",
            "TIJORI_PASSWORD": "must-not-be-read",
        }

        loaded = load_tijori_stdio_settings(environment)

        self.assertEqual(loaded.timeout_seconds, 20.0)
        self.assertEqual(loaded.max_concurrency, 2)
        self.assertEqual(
            loaded.provider_contract_version,
            "tijori.local_contract.v1",
        )
        self.assertNotIn("must-not-be-read", loaded.model_dump_json())

    def test_environment_failures_and_contract_mismatch_are_sanitized(self):
        for environment in (
            {},
            {"JARVIS_TIJORI_RUNTIME_EXECUTABLE": "relative-node"},
            {
                "JARVIS_TIJORI_RUNTIME_EXECUTABLE": "Bearer must-not-leak",
                "JARVIS_TIJORI_RUNTIME_SHA256": "bad",
                "JARVIS_TIJORI_SERVER_ENTRYPOINT": "bad",
                "JARVIS_TIJORI_SERVER_SHA256": "bad",
                "JARVIS_TIJORI_SESSION_ROOT": "bad",
            },
        ):
            with self.subTest(environment=tuple(environment)):
                with self.assertRaises(ConfigurationError) as caught:
                    load_tijori_stdio_settings(environment)
                self.assertNotIn("must-not-leak", str(caught.exception))

        with self.assertRaisesRegex(ConfigurationError, "do not match"):
            compose_tijori_fundamental_gateway(
                transport_settings=self.settings,
                adapter_settings=TijoriMcpAdapterSettings(
                    provider_contract_version="tijori.other_contract.v1"
                ),
                transport_builder=lambda settings: FakeTransport(),
            )


if __name__ == "__main__":
    unittest.main()
