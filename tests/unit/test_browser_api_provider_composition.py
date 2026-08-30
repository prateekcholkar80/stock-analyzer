import hashlib
from pathlib import Path
import tempfile
import unittest

from app.api.models import ProviderSessionTargetRequest
from app.exceptions import ConfigurationError
from app.fundamentals.provider_connections import (
    InMemoryProviderConnectionRegistry,
    ProviderConnectionResolutionError,
)
from app.services.provider_sessions import ProviderSessionService
from app.services.fundamental_evidence import (
    FundamentalEvidenceCoordinator,
    FundamentalEvidenceSource,
)
from app.storage.adapters.duckdb import DuckDBJarvisStorage
from tests.unit.test_fundamental_in_memory_repository import build_entry
from examples.browser_api import _provider_session_http_dependencies


class BrowserApiProviderCompositionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name).resolve()
        root.chmod(0o700)
        runtime = root / "node-24"
        runtime.write_text("synthetic node runtime", encoding="utf-8")
        runtime.chmod(0o700)
        server = root / "index.js"
        server.write_text("// synthetic MCP server", encoding="utf-8")
        server.chmod(0o600)
        self.environment = {
            "JARVIS_TIJORI_ENABLED": "true",
            "JARVIS_LOCAL_TENANT_ID": "tenant.prateek",
            "JARVIS_TIJORI_CONNECTION_ID": "provider.tijori.prateek",
            "JARVIS_TIJORI_ACCOUNT_REFERENCE_HASH": "a" * 64,
            "JARVIS_TIJORI_RUNTIME_EXECUTABLE": str(runtime),
            "JARVIS_TIJORI_RUNTIME_SHA256": hashlib.sha256(
                runtime.read_bytes()
            ).hexdigest(),
            "JARVIS_TIJORI_SERVER_ENTRYPOINT": str(server),
            "JARVIS_TIJORI_SERVER_SHA256": hashlib.sha256(
                server.read_bytes()
            ).hexdigest(),
            "JARVIS_TIJORI_SESSION_ROOT": str(root),
            "JARVIS_TIJORI_PROVIDER_CONTRACT_VERSION": (
                "tijori.local_contract.v1"
            ),
        }
        self.storage = DuckDBJarvisStorage(root / "jarvis.duckdb")

    def tearDown(self) -> None:
        self.storage.close()
        self.temporary.cleanup()

    def test_disabled_provider_sessions_add_no_http_dependencies(self):
        dependencies = _provider_session_http_dependencies(
            {
                "JARVIS_TIJORI_ENABLED": "false",
                "TIJORI_PASSWORD": "must-not-be-read",
            }
        )

        self.assertEqual(dependencies, {})

    def test_composes_service_registry_and_fixed_local_tenant_identity(self):
        stored = build_entry()
        self.storage.save_fundamental_snapshot(stored)
        dependencies = _provider_session_http_dependencies(
            self.environment,
            repository=self.storage,
        )

        self.assertIsInstance(
            dependencies["provider_sessions"],
            ProviderSessionService,
        )
        coordinator = dependencies["fundamental_evidence_coordinator"]
        self.assertIsInstance(coordinator, FundamentalEvidenceCoordinator)
        self.assertEqual(
            coordinator.load(stored.request).source,
            FundamentalEvidenceSource.CACHE,
        )
        registry = dependencies["browser_session_ownership"]
        self.assertIsInstance(registry, InMemoryProviderConnectionRegistry)
        self.assertIs(
            dependencies["provider_connection_scope_resolver"],
            registry,
        )
        tenant_id = dependencies["tenant_identity_resolver"](object())
        self.assertEqual(tenant_id, "tenant.prateek")

        registry.bind_browser_session(
            browser_session_id="session-prateek",
            tenant_id=tenant_id,
        )
        scope = registry(
            "session-prateek",
            ProviderSessionTargetRequest(
                provider_connection_id="provider.tijori.prateek",
                provider="tijori",
                account_reference_hash="a" * 64,
            ),
        )
        self.assertEqual(scope.tenant_id, "tenant.prateek")
        fundamental_scope = dependencies[
            "fundamental_provider_scope_resolver"
        ]("session-prateek")
        self.assertEqual(fundamental_scope, scope)

    def test_other_tenant_cannot_resolve_configured_local_connection(self):
        dependencies = _provider_session_http_dependencies(
            self.environment,
            repository=self.storage,
        )
        registry = dependencies["browser_session_ownership"]
        registry.bind_browser_session(
            browser_session_id="session-other",
            tenant_id="tenant.other",
        )

        with self.assertRaises(ProviderConnectionResolutionError):
            registry(
                "session-other",
                ProviderSessionTargetRequest(
                    provider_connection_id="provider.tijori.prateek",
                    provider="tijori",
                    account_reference_hash="a" * 64,
                ),
            )

    def test_invalid_enablement_and_identity_fail_without_secret_details(self):
        scenarios = (
            {**self.environment, "JARVIS_TIJORI_ENABLED": "yes"},
            {**self.environment, "JARVIS_LOCAL_TENANT_ID": ""},
            {
                **self.environment,
                "JARVIS_TIJORI_ACCOUNT_REFERENCE_HASH": "account-secret",
            },
        )
        for environment in scenarios:
            with self.subTest(keys=tuple(environment)):
                with self.assertRaises(ConfigurationError) as caught:
                    _provider_session_http_dependencies(
                        environment,
                        repository=self.storage,
                    )
                self.assertNotIn("account-secret", str(caught.exception))

    def test_enabled_provider_requires_existing_repository(self):
        with self.assertRaises(ConfigurationError):
            _provider_session_http_dependencies(self.environment)


if __name__ == "__main__":
    unittest.main()
