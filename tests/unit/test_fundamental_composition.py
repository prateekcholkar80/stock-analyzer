import hashlib
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from app.composition.fundamentals import (
    compose_corporate_filings_analysis_coordinator,
    compose_corporate_filings_analysis_workflow,
    compose_corporate_filings_analyst,
    compose_fundamental_analyst,
    compose_tijori_filing_evidence_coordinator,
    compose_tijori_fundamental_coordinator,
    compose_tijori_session_service,
    compose_tijori_session_provisioner,
    compose_tijori_fundamental_gateway,
    load_fundamental_filing_artifact_root,
    load_tijori_stdio_settings,
)
from app.agents.corporate_filings_analyst_agent import (
    CorporateFilingsAnalystAgent,
)
from app.agents.fundamental_analyst_agent import FundamentalAnalystAgent
from app.exceptions import ConfigurationError
from app.fundamentals.adapters.tijori_mcp import TijoriMcpAdapter
from app.fundamentals.filing_evidence_validator import (
    DeterministicFundamentalFilingEvidenceValidator,
)
from app.fundamentals.filing_extractor import (
    DeterministicFundamentalFilingExtractor,
)
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
from app.gateways.fundamentals import (
    FundamentalEvidenceGateway,
    FundamentalFilingGateway,
    FundamentalFinancialDocumentPackageGateway,
)
from app.llm.config import LLMRole, LLMSettings
from app.models.fundamental_frameworks import FundamentalFramework
from app.models.fundamentals import ProviderConnectionScope
from app.models.runtime_skill import RuntimeAgentRole
from app.services.provider_sessions import ProviderSessionService
from app.services.corporate_filings_analysis import (
    CorporateFilingsAnalysisCoordinator,
)
from app.services.corporate_filings_analysis_workflow import (
    CorporateFilingsAnalysisWorkflow,
)
from app.services.fundamental_evidence import (
    FundamentalEvidenceCoordinator,
    FundamentalEvidenceSource,
)
from app.services.fundamental_filing_evidence import (
    FundamentalFilingEvidenceCoordinator,
)
from app.storage.adapters.duckdb import DuckDBJarvisStorage
from app.storage.corporate_filings_analysis_repositories import (
    CorporateFilingsAnalysisRepository,
)
from app.storage.financial_document_repositories import (
    StructuredFinancialDocumentRepository,
)
from app.storage.peer_comparison_repositories import PeerComparisonRepository
from app.storage.benchmarking_financials_repositories import (
    BenchmarkingFinancialsRepository,
)
from app.storage.fundamental_package_repositories import (
    FundamentalPackageRepository,
)
from app.storage.fundamental_filing_evidence_repositories import (
    FundamentalFilingEvidenceRepository,
)
from app.storage.fundamental_filing_repositories import (
    FundamentalFilingRepository,
)
from tests.unit.test_financial_document_in_memory_repository import (
    build_entry as build_structured_entry,
)
from tests.unit.test_fundamental_in_memory_repository import build_entry
from tests.unit.test_peer_comparison_in_memory_repository import (
    build_entry as build_peer_comparison_entry,
)
from tests.unit.test_benchmarking_financials_in_memory_repository import (
    build_entry as build_benchmarking_financials_entry,
)


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


class FakeLLMGateway:
    def __init__(self, settings):
        self.settings = settings

    @property
    def configuration_fingerprint(self):
        return "f" * 64

    def generate(self, *, system, messages, response_model):
        raise AssertionError("Composition must not generate LLM output")


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
        self.filing_staging_root = root / "filing-staging"
        self.filing_staging_root.mkdir(mode=0o700)
        self.filing_artifact_root = root / "filing-artifacts"
        self.filing_artifact_root.mkdir(mode=0o700)
        self.filing_settings = self.settings.model_copy(
            update={
                "filing_staging_directory": self.filing_staging_root,
            }
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_composes_verified_role_bound_fundamental_analyst(self):
        received = []

        def builder(settings):
            received.append(settings)
            return FakeLLMGateway(settings)

        analyst = compose_fundamental_analyst(
            settings=LLMSettings.from_environment(
                {
                    "JARVIS_LLM_MODEL": "provider/shared",
                    "JARVIS_FUNDAMENTAL_ANALYST_LLM_MODEL": (
                        "provider/fundamental"
                    ),
                }
            ),
            gateway_builder=builder,
        )

        self.assertIsInstance(analyst, FundamentalAnalystAgent)
        self.assertEqual(len(received), 1)
        self.assertIs(received[0].role, LLMRole.FUNDAMENTAL_ANALYST)
        self.assertEqual(received[0].model, "provider/fundamental")
        self.assertIs(
            analyst.runtime_skill.contract.agent_role,
            RuntimeAgentRole.FUNDAMENTAL_ANALYST,
        )
        self.assertEqual(len(analyst.runtime_skill.prompt_content_sha256), 64)

    def test_fundamental_analyst_composition_fails_before_gateway_build(self):
        builds = []
        settings = LLMSettings.from_environment(
            {
                "JARVIS_BULL_LLM_MODEL": "provider/bull",
                "JARVIS_BEAR_LLM_MODEL": "provider/bear",
                "JARVIS_JUDGE_LLM_MODEL": "provider/judge",
            }
        )

        with self.assertRaisesRegex(ValueError, "fundamental_analyst"):
            compose_fundamental_analyst(
                settings=settings,
                gateway_builder=lambda role_settings: builds.append(
                    role_settings
                ),
            )

        self.assertEqual(builds, [])

        with self.assertRaisesRegex(ValueError, "runtime-skill loader"):
            compose_fundamental_analyst(
                settings=LLMSettings.from_environment(
                    {"JARVIS_LLM_MODEL": "provider/shared"}
                ),
                gateway_builder=lambda role_settings: builds.append(
                    role_settings
                ),
                runtime_skill_loader=object(),
            )

        self.assertEqual(builds, [])

    def test_composes_verified_role_bound_corporate_filings_analyst(self):
        received = []

        def builder(settings):
            received.append(settings)
            return FakeLLMGateway(settings)

        analyst = compose_corporate_filings_analyst(
            settings=LLMSettings.from_environment(
                {
                    "JARVIS_LLM_MODEL": "provider/shared",
                    "JARVIS_CORPORATE_FILINGS_ANALYST_LLM_MODEL": (
                        "provider/corporate-filings"
                    ),
                    "JARVIS_CORPORATE_FILINGS_ANALYST_LLM_MAX_TOKENS": "3200",
                }
            ),
            gateway_builder=builder,
        )

        self.assertIsInstance(analyst, CorporateFilingsAnalystAgent)
        self.assertEqual(len(received), 1)
        self.assertIs(
            received[0].role,
            LLMRole.CORPORATE_FILINGS_ANALYST,
        )
        self.assertEqual(received[0].model, "provider/corporate-filings")
        self.assertIs(
            analyst.framework_skill.contract.framework,
            FundamentalFramework.JHUNJHUNWALA_STYLE,
        )
        self.assertIsNotNone(analyst.framework_skill.doctrine)
        self.assertEqual(len(analyst.framework_skill.skill_content_sha256), 64)
        self.assertEqual(len(analyst.prompt_sha256), 64)
        self.assertEqual(
            analyst.build_analysis_policy().max_output_tokens,
            3_200,
        )
        self.assertEqual(
            analyst.build_analysis_policy().model_configuration_fingerprint,
            "f" * 64,
        )

    def test_corporate_filings_composition_fails_before_gateway_build(self):
        builds = []
        settings = LLMSettings.from_environment(
            {
                "JARVIS_BULL_LLM_MODEL": "provider/bull",
                "JARVIS_BEAR_LLM_MODEL": "provider/bear",
                "JARVIS_JUDGE_LLM_MODEL": "provider/judge",
            }
        )

        with self.assertRaisesRegex(ValueError, "corporate_filings_analyst"):
            compose_corporate_filings_analyst(
                settings=settings,
                gateway_builder=lambda role_settings: builds.append(
                    role_settings
                ),
            )

        self.assertEqual(builds, [])

        with self.assertRaisesRegex(ValueError, "framework-skill loader"):
            compose_corporate_filings_analyst(
                settings=LLMSettings.from_environment(
                    {"JARVIS_LLM_MODEL": "provider/shared"}
                ),
                gateway_builder=lambda role_settings: builds.append(
                    role_settings
                ),
                framework_skill_loader=object(),
            )

        self.assertEqual(builds, [])

    def test_composes_filings_coordinator_with_existing_duckdb(self):
        received = []

        def builder(settings):
            received.append(settings)
            return FakeLLMGateway(settings)

        database = Path(self.temporary.name) / "filings-analysis.duckdb"
        with DuckDBJarvisStorage(database, clock=lambda: NOW) as repository:
            coordinator = compose_corporate_filings_analysis_coordinator(
                repository=repository,
                settings=LLMSettings.from_environment(
                    {
                        "JARVIS_LLM_MODEL": "provider/shared",
                        "JARVIS_CORPORATE_FILINGS_ANALYST_LLM_MODEL": (
                            "provider/corporate-filings"
                        ),
                    }
                ),
                gateway_builder=builder,
                clock=lambda: NOW,
            )

            self.assertIsInstance(
                repository,
                CorporateFilingsAnalysisRepository,
            )
            self.assertIsInstance(
                coordinator,
                CorporateFilingsAnalysisCoordinator,
            )
            self.assertIs(coordinator._repository, repository)
            self.assertIsInstance(
                coordinator._analyzer,
                CorporateFilingsAnalystAgent,
            )
            self.assertEqual(len(received), 1)
            self.assertIs(
                received[0].role,
                LLMRole.CORPORATE_FILINGS_ANALYST,
            )
            self.assertEqual(
                received[0].model,
                "provider/corporate-filings",
            )

    def test_filings_coordinator_rejects_repository_before_gateway_build(self):
        builds = []

        with self.assertRaisesRegex(TypeError, "requires its repository"):
            compose_corporate_filings_analysis_coordinator(
                repository=object(),
                settings=LLMSettings.from_environment(
                    {"JARVIS_LLM_MODEL": "provider/shared"}
                ),
                gateway_builder=lambda settings: builds.append(settings),
            )

        self.assertEqual(builds, [])

    def test_composes_single_filings_workflow_with_existing_duckdb(self):
        received = []

        def builder(settings):
            received.append(settings)
            return FakeLLMGateway(settings)

        database = Path(self.temporary.name) / "filings-workflow.duckdb"
        with DuckDBJarvisStorage(database, clock=lambda: NOW) as storage:
            workflow = compose_corporate_filings_analysis_workflow(
                storage=storage,
                settings=LLMSettings.from_environment(
                    {
                        "JARVIS_LLM_MODEL": "provider/shared",
                        "JARVIS_CORPORATE_FILINGS_ANALYST_LLM_MODEL": (
                            "provider/corporate-filings"
                        ),
                    }
                ),
                gateway_builder=builder,
                clock=lambda: NOW,
            )

            self.assertIsInstance(workflow, CorporateFilingsAnalysisWorkflow)
            self.assertIs(workflow._assembler._filing_repository, storage)
            self.assertIs(workflow._assembler._evidence_repository, storage)
            self.assertIs(workflow._coordinator._repository, storage)
            self.assertIs(workflow._analyst, workflow._assembler._policy_provider)
            self.assertIs(workflow._analyst, workflow._coordinator._analyzer)
            self.assertEqual(len(received), 1)
            self.assertIs(
                received[0].role,
                LLMRole.CORPORATE_FILINGS_ANALYST,
            )

    def test_filings_workflow_rejects_storage_before_gateway_build(self):
        builds = []

        with self.assertRaisesRegex(
            TypeError,
            "filing artifact/package storage",
        ):
            compose_corporate_filings_analysis_workflow(
                storage=object(),
                settings=LLMSettings.from_environment(
                    {"JARVIS_LLM_MODEL": "provider/shared"}
                ),
                gateway_builder=lambda settings: builds.append(settings),
            )

        self.assertEqual(builds, [])

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
        self.assertIsInstance(
            gateway,
            FundamentalFinancialDocumentPackageGateway,
        )
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

    def test_composes_secure_filing_importer_without_provider_io(self):
        gateway = compose_tijori_fundamental_gateway(
            transport_settings=self.filing_settings,
            filing_artifact_root=self.filing_artifact_root,
            clock=lambda: NOW,
            transport_builder=lambda settings: FakeTransport(),
        )

        self.assertIsInstance(gateway, TijoriMcpAdapter)
        self.assertIsNotNone(gateway._filing_artifact_importer)
        self.assertRegex(
            gateway._filing_artifact_importer.configuration_fingerprint,
            r"^[a-f0-9]{64}$",
        )

    def test_composes_cache_first_filing_pipeline_with_existing_duckdb(self):
        transports = []

        def builder(settings):
            transports.append(settings)
            return FakeTransport()

        database = Path(self.temporary.name) / "filing-evidence.duckdb"
        with DuckDBJarvisStorage(database, clock=lambda: NOW) as storage:
            coordinator = compose_tijori_filing_evidence_coordinator(
                transport_settings=self.filing_settings,
                storage=storage,
                filing_artifact_root=self.filing_artifact_root,
                clock=lambda: NOW,
                transport_builder=builder,
            )

            self.assertIsInstance(storage, FundamentalFilingRepository)
            self.assertIsInstance(
                storage,
                FundamentalFilingEvidenceRepository,
            )
            self.assertIsInstance(
                coordinator,
                FundamentalFilingEvidenceCoordinator,
            )
            self.assertIsInstance(coordinator._gateway, FundamentalFilingGateway)
            self.assertIsInstance(coordinator._gateway, TijoriMcpAdapter)
            self.assertIsNotNone(
                coordinator._gateway._filing_artifact_importer
            )
            self.assertIs(coordinator._filing_repository, storage)
            self.assertIs(coordinator._evidence_repository, storage)
            self.assertEqual(
                coordinator._artifact_verifier,
                coordinator._extractor.artifact_is_intact,
            )
            self.assertEqual(
                coordinator._artifact_reconciler,
                coordinator._extractor.reconcile_page_count,
            )
            self.assertIsInstance(
                coordinator._extractor,
                DeterministicFundamentalFilingExtractor,
            )
            self.assertEqual(
                coordinator._extractor._artifact_root,
                self.filing_artifact_root,
            )
            self.assertIsInstance(
                coordinator._validator,
                DeterministicFundamentalFilingEvidenceValidator,
            )

        self.assertEqual(transports, [self.filing_settings])

    def test_filing_pipeline_rejects_storage_before_gateway_build(self):
        transports = []

        with self.assertRaisesRegex(
            TypeError,
            "filing artifact/package storage",
        ):
            compose_tijori_filing_evidence_coordinator(
                transport_settings=self.filing_settings,
                storage=object(),
                filing_artifact_root=self.filing_artifact_root,
                transport_builder=lambda settings: transports.append(settings),
            )

        self.assertEqual(transports, [])

    def test_filing_pipeline_requires_gateway_filing_capability(self):
        database = Path(self.temporary.name) / "invalid-filing-gateway.duckdb"
        with DuckDBJarvisStorage(database, clock=lambda: NOW) as storage:
            with patch(
                "app.composition.fundamentals.compose_tijori_fundamental_gateway",
                return_value=object(),
            ):
                with self.assertRaisesRegex(
                    ConfigurationError,
                    "filing discovery and acquisition",
                ):
                    compose_tijori_filing_evidence_coordinator(
                        transport_settings=self.filing_settings,
                        storage=storage,
                        filing_artifact_root=self.filing_artifact_root,
                    )

    def test_filing_roots_are_all_or_nothing_and_fail_closed(self):
        for settings, artifact_root in (
            (self.filing_settings, None),
            (self.settings, self.filing_artifact_root),
        ):
            with self.subTest(
                staging=settings.filing_staging_directory is not None
            ):
                with self.assertRaisesRegex(
                    ConfigurationError,
                    "configured together",
                ):
                    compose_tijori_fundamental_gateway(
                        transport_settings=settings,
                        filing_artifact_root=artifact_root,
                        transport_builder=lambda value: FakeTransport(),
                    )

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

            self.assertIsInstance(repository, FundamentalPackageRepository)
            self.assertIs(coordinator._package_repository, repository)
            self.assertIsInstance(
                repository,
                FundamentalFilingEvidenceRepository,
            )
            self.assertIs(
                coordinator._filing_evidence_repository,
                repository,
            )
            self.assertIs(
                coordinator._financial_document_package_gateway,
                coordinator._gateway,
            )

        self.assertIsInstance(coordinator, FundamentalEvidenceCoordinator)
        self.assertEqual(result.source, FundamentalEvidenceSource.CACHE)
        self.assertEqual(result.stored_snapshot, stored)

    def test_coordinator_bootstrap_rejects_gateway_without_package_capability(self):
        database = Path(self.temporary.name) / "missing-package-gateway.duckdb"
        with DuckDBJarvisStorage(database, clock=lambda: NOW) as repository:
            with patch(
                "app.composition.fundamentals.compose_tijori_fundamental_gateway",
                return_value=object(),
            ):
                with self.assertRaisesRegex(
                    ConfigurationError,
                    "complete financial package retrieval",
                ):
                    compose_tijori_fundamental_coordinator(
                        transport_settings=self.settings,
                        repository=repository,
                        clock=lambda: NOW,
                    )

    def test_reuses_same_open_duckdb_for_structured_document_cache(self):
        database = Path(self.temporary.name) / "structured-fundamentals.duckdb"
        stored = build_structured_entry()
        clock = lambda: stored.stored_at + timedelta(hours=1)
        with DuckDBJarvisStorage(database, clock=clock) as repository:
            repository.save_structured_financial_document(stored)
            coordinator = compose_tijori_fundamental_coordinator(
                transport_settings=self.settings,
                repository=repository,
                clock=clock,
                transport_builder=lambda settings: FakeTransport(),
            )

            loaded = coordinator.load_structured_document(stored.request)

            self.assertIsInstance(
                repository,
                StructuredFinancialDocumentRepository,
            )
            self.assertIs(
                coordinator._structured_document_repository,
                repository,
            )

        self.assertIs(loaded.source, FundamentalEvidenceSource.CACHE)
        self.assertEqual(loaded.stored_document, stored)

    def test_reuses_same_open_duckdb_for_peer_comparison_cache(self):
        database = Path(self.temporary.name) / "peer-comparisons.duckdb"
        stored = build_peer_comparison_entry()
        clock = lambda: stored.stored_at + timedelta(hours=1)
        with DuckDBJarvisStorage(database, clock=clock) as repository:
            repository.save_peer_comparison(stored)
            coordinator = compose_tijori_fundamental_coordinator(
                transport_settings=self.settings,
                repository=repository,
                clock=clock,
                transport_builder=lambda settings: FakeTransport(),
            )

            loaded = coordinator.load_peer_comparison(stored.request)

            self.assertIsInstance(repository, PeerComparisonRepository)
            self.assertIs(coordinator._peer_comparison_repository, repository)

        self.assertIs(loaded.source, FundamentalEvidenceSource.CACHE)
        self.assertEqual(loaded.stored_document, stored)

    def test_reuses_same_open_duckdb_for_benchmarking_financials_cache(self):
        database = Path(self.temporary.name) / "benchmarking.duckdb"
        stored = build_benchmarking_financials_entry()
        clock = lambda: stored.stored_at + timedelta(hours=1)
        with DuckDBJarvisStorage(database, clock=clock) as repository:
            repository.save_benchmarking_financials(stored)
            coordinator = compose_tijori_fundamental_coordinator(
                transport_settings=self.settings,
                repository=repository,
                clock=clock,
                transport_builder=lambda settings: FakeTransport(),
            )

            loaded = coordinator.load_benchmarking_financials(stored.request)

            self.assertIsInstance(
                repository,
                BenchmarkingFinancialsRepository,
            )
            self.assertIs(
                coordinator._benchmarking_financials_repository,
                repository,
            )

        self.assertIs(loaded.source, FundamentalEvidenceSource.CACHE)
        self.assertEqual(loaded.stored_document, stored)

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
            "JARVIS_TIJORI_FILING_STAGING_DIRECTORY": str(
                self.filing_staging_root
            ),
            "JARVIS_FUNDAMENTAL_FILING_ARTIFACT_DIRECTORY": str(
                self.filing_artifact_root
            ),
            "TIJORI_PASSWORD": "must-not-be-read",
        }

        loaded = load_tijori_stdio_settings(environment)

        self.assertEqual(loaded.timeout_seconds, 20.0)
        self.assertEqual(loaded.max_concurrency, 2)
        self.assertEqual(
            loaded.provider_contract_version,
            "tijori.local_contract.v1",
        )
        self.assertEqual(
            loaded.filing_staging_directory,
            self.filing_staging_root,
        )
        self.assertEqual(
            load_fundamental_filing_artifact_root(environment),
            self.filing_artifact_root,
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

        for value in (None, "", "relative-artifacts"):
            with self.subTest(filing_artifact_root=value):
                with self.assertRaises(ConfigurationError):
                    load_fundamental_filing_artifact_root(
                        {
                            "JARVIS_FUNDAMENTAL_FILING_ARTIFACT_DIRECTORY": (
                                value
                            )
                        }
                        if value is not None
                        else {}
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
