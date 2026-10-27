"""Composition root for provider-neutral fundamental evidence gateways."""

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
import os
from pathlib import Path
import stat

from pydantic import ValidationError

from app.agents.corporate_filings_analyst_agent import (
    CorporateFilingsAnalystAgent,
)
from app.agents.fundamental_analyst_agent import FundamentalAnalystAgent
from app.exceptions import ConfigurationError
from app.fundamental_framework_skill_loader import (
    FundamentalFrameworkSkillLoader,
)
from app.fundamentals.adapters.tijori_mcp import (
    TijoriFilingArtifactImporter,
    TijoriMcpAdapter,
)
from app.fundamentals.filing_evidence_validator import (
    DeterministicFundamentalFilingEvidenceValidator,
)
from app.fundamentals.filing_extractor import (
    DeterministicFundamentalFilingExtractor,
)
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
from app.gateways.fundamentals import (
    FundamentalEvidenceGateway,
    FundamentalFilingGateway,
    FundamentalFinancialDocumentPackageGateway,
)
from app.llm.config import LLMRole, LLMSettings, get_llm_settings
from app.llm.factory import GatewayBuilder, LLMGatewayFactory
from app.models.fundamental_storage import FUNDAMENTAL_MAX_RETENTION
from app.models.fundamental_framework_skill import (
    FundamentalFrameworkSkillContract,
    FundamentalFrameworkSkillRegistry,
    REQUIRED_FRAMEWORK_SKILL_ABSTENTIONS,
    REQUIRED_FRAMEWORK_SKILL_CITATIONS,
    REQUIRED_FRAMEWORK_SKILL_EVIDENCE,
)
from app.models.fundamental_frameworks import FundamentalFramework
from app.models.runtime_skill import RuntimeAgentRole
from app.runtime_skill_loader import RuntimeSkillLoader
from app.services.corporate_filings_analysis import (
    CorporateFilingsAnalysisCoordinator,
)
from app.services.corporate_filings_analysis_workflow import (
    CorporateFilingsAnalysisWorkflow,
)
from app.services.fundamental_evidence import FundamentalEvidenceCoordinator
from app.services.fundamental_filing_evidence import (
    FundamentalFilingEvidenceCoordinator,
)
from app.services.provider_sessions import ProviderSessionService
from app.storage.corporate_filings_analysis_repositories import (
    CorporateFilingsAnalysisRepository,
)
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
from app.storage.fundamental_package_repositories import (
    FundamentalPackageRepository,
)
from app.storage.fundamental_filing_evidence_repositories import (
    FundamentalFilingEvidenceRepository,
)
from app.storage.fundamental_filing_repositories import (
    FundamentalFilingRepository,
)


TijoriTransportBuilder = Callable[
    [TijoriStdioMcpSettings],
    TijoriMcpTransport,
]
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_RUNTIME_SKILL_MANIFEST = (
    _REPOSITORY_ROOT / "runtime-skills" / "manifest.yaml"
)
_FUNDAMENTAL_FRAMEWORK_SKILL_MANIFEST = (
    _REPOSITORY_ROOT
    / "runtime-skills"
    / "fundamental-frameworks"
    / "manifest.yaml"
)
_CORPORATE_FILINGS_FRAMEWORK_SKILL_REGISTRY = (
    FundamentalFrameworkSkillRegistry(
        skills=(
            FundamentalFrameworkSkillContract(
                skill_id=(
                    "jarvis.fundamental_framework."
                    "jhunjhunwala_style.v1"
                ),
                framework=FundamentalFramework.JHUNJHUNWALA_STYLE,
                version="1.0.0",
                purpose=(
                    "Interpret released corporate-filing evidence through "
                    "the approved Jhunjhunwala-style public-principles "
                    "doctrine."
                ),
                score_policy_id=(
                    "jarvis.framework_score.jhunjhunwala_style"
                ),
                score_policy_version="1.0.0",
                doctrine_id=(
                    "jarvis.fundamental_framework_doctrine."
                    "jhunjhunwala_style.v1"
                ),
                doctrine_version="1.0.0",
                allowed_evidence=REQUIRED_FRAMEWORK_SKILL_EVIDENCE,
                required_citations=REQUIRED_FRAMEWORK_SKILL_CITATIONS,
                abstain_on=REQUIRED_FRAMEWORK_SKILL_ABSTENTIONS,
            ),
        )
    )
)
_PROVISIONING_CLI = Path(
    "integrations/tijori-mcp/src/provision-session-cli.js"
)
_FILING_STAGING_ENVIRONMENT_KEY = (
    "JARVIS_TIJORI_FILING_STAGING_DIRECTORY"
)
_FILING_ARTIFACT_ENVIRONMENT_KEY = (
    "JARVIS_FUNDAMENTAL_FILING_ARTIFACT_DIRECTORY"
)


def compose_fundamental_analyst(
    *,
    settings: LLMSettings | None = None,
    gateway_builder: GatewayBuilder | None = None,
    runtime_skill_loader: RuntimeSkillLoader | None = None,
) -> FundamentalAnalystAgent:
    """Bind the dedicated LLM gateway to its verified static skill."""

    resolved_settings = settings or get_llm_settings()
    if not isinstance(resolved_settings, LLMSettings):
        raise ValueError(
            "fundamental analyst composition requires validated LLM settings"
        )
    resolved_loader = (
        RuntimeSkillLoader(_RUNTIME_SKILL_MANIFEST)
        if runtime_skill_loader is None
        else runtime_skill_loader
    )
    if not isinstance(resolved_loader, RuntimeSkillLoader):
        raise ValueError(
            "fundamental analyst composition requires a validated "
            "runtime-skill loader"
        )
    runtime_skills = resolved_loader.load()
    gateway_factory = (
        LLMGatewayFactory(resolved_settings)
        if gateway_builder is None
        else LLMGatewayFactory(resolved_settings, gateway_builder)
    )
    return FundamentalAnalystAgent(
        gateway_factory.for_role(LLMRole.FUNDAMENTAL_ANALYST),
        runtime_skill=runtime_skills.skill_for(
            RuntimeAgentRole.FUNDAMENTAL_ANALYST
        ),
    )


def compose_corporate_filings_analyst(
    *,
    settings: LLMSettings | None = None,
    gateway_builder: GatewayBuilder | None = None,
    framework_skill_loader: FundamentalFrameworkSkillLoader | None = None,
) -> CorporateFilingsAnalystAgent:
    """Bind the dedicated LLM role to one exact approved framework skill."""

    resolved_settings = settings or get_llm_settings()
    if not isinstance(resolved_settings, LLMSettings):
        raise ValueError(
            "corporate filings analyst composition requires validated "
            "LLM settings"
        )
    resolved_loader = (
        FundamentalFrameworkSkillLoader(
            _FUNDAMENTAL_FRAMEWORK_SKILL_MANIFEST,
            registry=_CORPORATE_FILINGS_FRAMEWORK_SKILL_REGISTRY,
        )
        if framework_skill_loader is None
        else framework_skill_loader
    )
    if not isinstance(resolved_loader, FundamentalFrameworkSkillLoader):
        raise ValueError(
            "corporate filings analyst composition requires a validated "
            "framework-skill loader"
        )
    framework_skills = resolved_loader.load()
    framework_skill = framework_skills.skill_for(
        FundamentalFramework.JHUNJHUNWALA_STYLE
    )
    role_settings = resolved_settings.for_role(
        LLMRole.CORPORATE_FILINGS_ANALYST
    )
    gateway_factory = (
        LLMGatewayFactory(resolved_settings)
        if gateway_builder is None
        else LLMGatewayFactory(resolved_settings, gateway_builder)
    )
    return CorporateFilingsAnalystAgent(
        gateway_factory.for_role(LLMRole.CORPORATE_FILINGS_ANALYST),
        framework_skill=framework_skill,
        max_output_tokens=role_settings.max_tokens,
    )


def compose_corporate_filings_analysis_coordinator(
    *,
    repository: CorporateFilingsAnalysisRepository,
    settings: LLMSettings | None = None,
    gateway_builder: GatewayBuilder | None = None,
    framework_skill_loader: FundamentalFrameworkSkillLoader | None = None,
    clock: Callable[[], datetime] | None = None,
) -> CorporateFilingsAnalysisCoordinator:
    """Compose grounded filing analysis against caller-owned storage.

    The repository lifecycle remains with the application bootstrap. This
    allows the browser runtime to reuse its existing ``DuckDBJarvisStorage``
    instance without opening another database connection. Composition loads
    and verifies the approved framework skill, but performs no LLM call.
    """

    if not isinstance(repository, CorporateFilingsAnalysisRepository):
        raise TypeError(
            "corporate filings analysis composition requires its repository"
        )
    analyzer = compose_corporate_filings_analyst(
        settings=settings,
        gateway_builder=gateway_builder,
        framework_skill_loader=framework_skill_loader,
    )
    return CorporateFilingsAnalysisCoordinator(
        analyzer=analyzer,
        repository=repository,
        clock=clock,
    )


def compose_corporate_filings_analysis_workflow(
    *,
    storage: object,
    settings: LLMSettings | None = None,
    gateway_builder: GatewayBuilder | None = None,
    framework_skill_loader: FundamentalFrameworkSkillLoader | None = None,
    clock: Callable[[], datetime] | None = None,
) -> CorporateFilingsAnalysisWorkflow:
    """Compose one filing-analysis workflow around caller-owned storage.

    The supplied object must implement all three provider-neutral persistence
    ports. The verified analyst is constructed exactly once and serves as both
    the immutable policy provider and the grounded analysis generator.
    Composition performs no repository write, provider call, or LLM request.
    """

    required_ports = (
        (FundamentalFilingRepository, "filing artifact/package storage"),
        (FundamentalFilingEvidenceRepository, "filing evidence storage"),
        (CorporateFilingsAnalysisRepository, "filing analysis storage"),
    )
    for repository_port, description in required_ports:
        if not isinstance(storage, repository_port):
            raise TypeError(
                "corporate filings workflow composition requires "
                f"{description}"
            )

    analyst = compose_corporate_filings_analyst(
        settings=settings,
        gateway_builder=gateway_builder,
        framework_skill_loader=framework_skill_loader,
    )
    return CorporateFilingsAnalysisWorkflow(
        analyst=analyst,
        filing_repository=storage,
        evidence_repository=storage,
        analysis_repository=storage,
        clock=clock,
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
        filing_staging_directory = source.get(
            _FILING_STAGING_ENVIRONMENT_KEY
        )
        if filing_staging_directory is not None and (
            filing_staging_directory.strip()
        ):
            values["filing_staging_directory"] = Path(
                filing_staging_directory.strip()
            )
        return TijoriStdioMcpSettings.model_validate(values)
    except (TypeError, ValueError, ValidationError) as exc:
        raise ConfigurationError(
            "Tijori local runtime configuration is missing or invalid"
        ) from exc


def load_fundamental_filing_artifact_root(
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Load the non-secret durable filing root for composition injection."""

    source = environment if environment is not None else os.environ
    try:
        value = source.get(_FILING_ARTIFACT_ENVIRONMENT_KEY)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("required filing artifact root is missing")
        root = Path(value.strip())
        if not root.is_absolute():
            raise ValueError("filing artifact root must be absolute")
        return root
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(
            "Fundamental filing artifact configuration is missing or invalid"
        ) from exc


def compose_tijori_fundamental_gateway(
    *,
    transport_settings: TijoriStdioMcpSettings,
    adapter_settings: TijoriMcpAdapterSettings | None = None,
    clock: Callable[[], datetime] | None = None,
    transport_builder: TijoriTransportBuilder | None = None,
    filing_artifact_root: Path | None = None,
) -> FundamentalEvidenceGateway:
    """Compose Tijori behind the provider-neutral read-only gateway.

    Composition validates pinned runtime paths through the transport
    constructor but does not start a process, browser, session, or provider
    request. Session identity remains supplied later through each
    ``ProviderConnectionScope``.
    """

    if not isinstance(transport_settings, TijoriStdioMcpSettings):
        raise TypeError("Tijori composition requires stdio transport settings")

    filing_staging_root = transport_settings.filing_staging_directory
    if (filing_staging_root is None) != (filing_artifact_root is None):
        raise ConfigurationError(
            "Tijori filing staging and artifact roots must be configured together"
        )
    filing_artifact_importer = None
    if filing_staging_root is not None and filing_artifact_root is not None:
        try:
            filing_artifact_importer = TijoriFilingArtifactImporter(
                staging_root=filing_staging_root,
                artifact_root=filing_artifact_root,
            )
        except (OSError, TypeError, ValueError) as exc:
            raise ConfigurationError(
                "Tijori filing artifact configuration is invalid"
            ) from exc

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
        filing_artifact_importer=filing_artifact_importer,
    )


def compose_tijori_fundamental_coordinator(
    *,
    transport_settings: TijoriStdioMcpSettings,
    repository: FundamentalSnapshotRepository,
    adapter_settings: TijoriMcpAdapterSettings | None = None,
    clock: Callable[[], datetime] | None = None,
    retention: timedelta = FUNDAMENTAL_MAX_RETENTION,
    transport_builder: TijoriTransportBuilder | None = None,
    filing_artifact_root: Path | None = None,
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
    if not isinstance(repository, FundamentalPackageRepository):
        raise TypeError(
            "Tijori coordinator composition requires atomic package storage"
        )
    if not isinstance(repository, FundamentalFilingEvidenceRepository):
        raise TypeError(
            "Tijori coordinator composition requires filing evidence storage"
        )
    gateway = compose_tijori_fundamental_gateway(
        transport_settings=transport_settings,
        adapter_settings=adapter_settings,
        clock=clock,
        transport_builder=transport_builder,
        filing_artifact_root=filing_artifact_root,
    )
    if not isinstance(gateway, FundamentalFinancialDocumentPackageGateway):
        raise ConfigurationError(
            "Tijori gateway does not provide complete financial package "
            "retrieval"
        )
    return FundamentalEvidenceCoordinator(
        gateway=gateway,
        financial_document_package_gateway=gateway,
        repository=repository,
        structured_document_repository=repository,
        peer_comparison_repository=repository,
        benchmarking_financials_repository=repository,
        package_repository=repository,
        filing_evidence_repository=repository,
        clock=clock,
        retention=retention,
    )


def compose_tijori_filing_evidence_coordinator(
    *,
    transport_settings: TijoriStdioMcpSettings,
    storage: object,
    filing_artifact_root: Path,
    adapter_settings: TijoriMcpAdapterSettings | None = None,
    clock: Callable[[], datetime] | None = None,
    transport_builder: TijoriTransportBuilder | None = None,
) -> FundamentalFilingEvidenceCoordinator:
    """Compose cache-first filing preparation around caller-owned storage.

    The same secure durable root is supplied to the Tijori artifact importer
    and deterministic PDF extractor. The caller retains ownership of the
    already-open storage lifecycle. Composition performs no provider call,
    file extraction, repository write, or LLM request.
    """

    if not isinstance(storage, FundamentalFilingRepository):
        raise TypeError(
            "Tijori filing composition requires filing artifact/package "
            "storage"
        )
    if not isinstance(storage, FundamentalFilingEvidenceRepository):
        raise TypeError(
            "Tijori filing composition requires filing evidence storage"
        )

    gateway = compose_tijori_fundamental_gateway(
        transport_settings=transport_settings,
        adapter_settings=adapter_settings,
        clock=clock,
        transport_builder=transport_builder,
        filing_artifact_root=filing_artifact_root,
    )
    if not isinstance(gateway, FundamentalFilingGateway):
        raise ConfigurationError(
            "Tijori gateway does not provide filing discovery and acquisition"
        )
    try:
        extractor = DeterministicFundamentalFilingExtractor(
            artifact_root=filing_artifact_root,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise ConfigurationError(
            "Fundamental filing extraction configuration is invalid"
        ) from exc

    return FundamentalFilingEvidenceCoordinator(
        gateway=gateway,
        filing_repository=storage,
        evidence_repository=storage,
        extractor=extractor,
        validator=DeterministicFundamentalFilingEvidenceValidator(),
        artifact_verifier=extractor.artifact_is_intact,
        artifact_reconciler=extractor.reconcile_page_count,
        clock=clock,
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
