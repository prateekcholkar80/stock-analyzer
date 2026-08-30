import hashlib
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from app.composition.fundamentals import (
    compose_tijori_fundamental_coordinator,
    compose_tijori_session_service,
    compose_tijori_session_provisioner,
    compose_tijori_fundamental_gateway,
    load_tijori_stdio_settings,
)
from app.exceptions import ConfigurationError
from app.fundamentals.adapters.tijori_mcp import TijoriMcpAdapter
from app.fundamentals.session_provisioning import (
    ProviderSessionInspectionRequest,
    ProviderSessionProvisioner,
    ProviderSessionProvisioningRequest,
    ProviderSessionStatus,
)
from app.fundamentals.tijori_mcp_contracts import (
    TijoriMcpAdapterSettings,
    TijoriMcpToolResult,
    TijoriMcpTransportInspection,
)
from app.fundamentals.transports.stdio_mcp import TijoriStdioMcpSettings
from app.gateways.fundamentals import FundamentalEvidenceGateway
from app.models.fundamentals import ProviderConnectionScope
from app.services.provider_sessions import ProviderSessionService
from app.services.fundamental_evidence import (
    FundamentalEvidenceCoordinator,
    FundamentalEvidenceSource,
)
from app.storage.adapters.duckdb import DuckDBJarvisStorage
from tests.unit.test_fundamental_in_memory_repository import build_entry


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
        root = Path(self.temporary.name).resolve()
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
        self.repository_root = root / "repository"
        self.repository_root.mkdir()
        self.repository_root = self.repository_root.resolve(strict=True)
        cli = (
            self.repository_root
            / "integrations/tijori-mcp/src/provision-session-cli.js"
        )
        cli.parent.mkdir(parents=True)
        cli.write_text("// synthetic provisioning CLI", encoding="utf-8")
        cli.chmod(0o600)

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

    def test_composes_coordinator_with_existing_duckdb_and_reuses_cache(self):
        database = Path(self.temporary.name) / "fundamentals.duckdb"
        stored = build_entry()
        with DuckDBJarvisStorage(database, clock=lambda: NOW) as repository:
            repository.save_fundamental_snapshot(stored)
            coordinator = compose_tijori_fundamental_coordinator(
                transport_settings=self.settings,
                repository=repository,
                clock=lambda: NOW,
                transport_builder=lambda settings: FakeTransport(),
            )

            result = coordinator.load(stored.request)

        self.assertIsInstance(coordinator, FundamentalEvidenceCoordinator)
        self.assertEqual(result.source, FundamentalEvidenceSource.CACHE)
        self.assertEqual(result.stored_snapshot, stored)

    def test_coordinator_composition_rejects_invalid_repository_before_io(self):
        transport_builds = []

        with self.assertRaisesRegex(TypeError, "fundamental repository"):
            compose_tijori_fundamental_coordinator(
                transport_settings=self.settings,
                repository="not-a-repository",
                transport_builder=lambda settings: transport_builds.append(
                    settings
                ),
            )

        self.assertEqual(transport_builds, [])

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

    def test_composes_session_provisioner_with_pinned_runtime_and_scoped_path(self):
        calls = []

        def runner(command, **options):
            calls.append((command, options))
            target = Path(options["env"]["JARVIS_TIJORI_SESSION_FILE"])
            content = b'{"cookies":[],"origins":[]}'
            target.write_bytes(content)
            target.chmod(0o600)
            os.utime(target, (NOW.timestamp(), NOW.timestamp()))
            response = {
                "session_reference_hash": hashlib.sha256(content).hexdigest(),
                "status": "ready",
            }
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=f"{json.dumps(response, separators=(',', ':'))}\n",
            )

        provisioner = compose_tijori_session_provisioner(
            transport_settings=self.settings,
            repository_root=self.repository_root,
            clock=lambda: NOW,
            process_runner=runner,
        )
        scope = ProviderConnectionScope(
            tenant_id="tenant.prateek",
            provider_connection_id="provider.tijori.prateek",
            provider="tijori",
            account_reference_hash="b" * 64,
        )
        lifecycle = provisioner.provision(
            request=ProviderSessionProvisioningRequest(
                request_id="session.provision.composed",
                connection=scope,
                requested_at=NOW,
                user_interaction_authorized=True,
            )
        )

        self.assertIsInstance(provisioner, ProviderSessionProvisioner)
        self.assertEqual(lifecycle.status, ProviderSessionStatus.READY)
        self.assertEqual(calls[0][0][0], str(self.settings.runtime_executable))
        self.assertEqual(
            calls[0][0][1],
            str(
                self.repository_root
                / "integrations/tijori-mcp/src/provision-session-cli.js"
            ),
        )
        expected_digest = hashlib.sha256(
            (
                f"{scope.tenant_id}:{scope.provider_connection_id}:"
                f"{scope.account_reference_hash}"
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(
            Path(calls[0][1]["env"]["JARVIS_TIJORI_SESSION_FILE"]),
            self.settings.session_root / f"{expected_digest}.json",
        )

    def test_session_composition_rejects_untrusted_cli_without_provider_io(self):
        cli = (
            self.repository_root
            / "integrations/tijori-mcp/src/provision-session-cli.js"
        )
        for mutation in ("missing", "writable", "symlink"):
            with self.subTest(mutation=mutation):
                if cli.is_symlink() or cli.exists():
                    cli.unlink()
                if mutation == "writable":
                    cli.write_text("// writable", encoding="utf-8")
                    cli.chmod(0o622)
                elif mutation == "symlink":
                    target = self.repository_root / "target.js"
                    target.write_text("// target", encoding="utf-8")
                    cli.symlink_to(target)

                with self.assertRaises(ConfigurationError) as caught:
                    compose_tijori_session_provisioner(
                        transport_settings=self.settings,
                        repository_root=self.repository_root,
                    )
                self.assertNotIn(str(cli), str(caught.exception))

        with self.assertRaises(ConfigurationError):
            compose_tijori_session_provisioner(
                transport_settings=self.settings,
                repository_root=Path("relative"),
            )

        if cli.is_symlink() or cli.exists():
            cli.unlink()
        cli.write_text("// restored", encoding="utf-8")
        cli.chmod(0o600)
        invalid_runtime = self.settings.model_copy(
            update={"runtime_sha256": "0" * 64}
        )
        with self.assertRaises(ConfigurationError) as caught:
            compose_tijori_session_provisioner(
                transport_settings=invalid_runtime,
                repository_root=self.repository_root,
            )
        self.assertNotIn(str(self.settings.runtime_executable), str(caught.exception))

    def test_composes_provider_neutral_session_service_without_process_io(self):
        calls = []
        service = compose_tijori_session_service(
            transport_settings=self.settings,
            repository_root=self.repository_root,
            clock=lambda: NOW,
            process_runner=lambda *args, **kwargs: calls.append((args, kwargs)),
        )

        self.assertIsInstance(service, ProviderSessionService)
        self.assertRegex(service.configuration_fingerprint, r"^[a-f0-9]{64}$")
        lifecycle = service.status(
            request=ProviderSessionInspectionRequest(
                request_id="session.status.composed",
                connection=ProviderConnectionScope(
                    tenant_id="tenant.prateek",
                    provider_connection_id="provider.tijori.prateek",
                    provider="tijori",
                    account_reference_hash="b" * 64,
                ),
                requested_at=NOW,
            )
        )
        self.assertEqual(lifecycle.status, ProviderSessionStatus.UNCONFIGURED)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
