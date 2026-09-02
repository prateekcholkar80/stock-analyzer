from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from threading import Event, RLock
from typing import Protocol, runtime_checkable
from zoneinfo import ZoneInfo

from app.exceptions import (
    AmbiguousInstrumentError,
    ApplicationError,
    BrowserOperationConflictError,
    InstrumentMasterDownloadError,
    InstrumentNotFoundError,
    InstrumentResolutionError,
    IntentRecognitionError,
    LLMError,
    MarketDataError,
    FundamentalGatewayError,
)
from app.gateways.fundamentals import (
    FundamentalCompanyOverviewRequest,
    FundamentalFinancialsRequest,
    FundamentalIssuerLocator,
    FundamentalIssuerResolutionRequest,
    FundamentalShareholdingRequest,
    FundamentalStructuredDocumentRequest,
)
from app.logging_config import get_logger
from app.models.browser_operations import (
    BrowserBenchmarkingFinancialsReference,
    BrowserOperationFailure,
    BrowserOperationKind,
    BrowserOperationOutput,
    BrowserOperationRequest,
    BrowserOperationSnapshot,
    BrowserOperationStatus,
    BrowserSessionSnapshot,
    BrowserStructuredDocumentReference,
    WorkflowEventBatch,
    WorkflowEventReplayCursor,
)
from app.models.debate import AgenticDebateResult, JudgeFollowUpAnswer
from app.models.interaction import (
    JarvisCommandStatus,
    JarvisSwingAnalysisResponse,
    SwingAnalysisCommand,
)
from app.models.multi_timeframe_evidence import MultiTimeframeEvidenceReview
from app.models.fundamentals import ProviderConnectionScope
from app.models.financial_documents import (
    FinancialDocumentType,
    FinancialReportingBasis,
)
from app.models.presentation import (
    JarvisMultiTimeframeResearchExplanation,
    JarvisResearchExplanation,
)
from app.models.storage import (
    EndToEndSwingAnalysisResult,
    MultiTimeframeEndToEndSwingAnalysisResult,
)
from app.models.workflow import WorkflowEventState, WorkflowStage
from app.services.fundamental_evidence import (
    BenchmarkingFinancialsLoadResult,
    FundamentalEvidenceLoadResult,
    FundamentalIssuerLoadResult,
    StructuredDocumentLoadResult,
)
from app.workflow.events import WorkflowEventEmitter
from app.workflow.operations import BrowserOperationRegistry


IST = ZoneInfo("Asia/Kolkata")
RunnerClock = Callable[[], datetime]
logger = get_logger("workflow.browser_runner")


@runtime_checkable
class BrowserOperationResultStore(Protocol):
    def save(self, output: BrowserOperationOutput) -> None:
        ...

    def get(self, operation_id: str) -> BrowserOperationOutput | None:
        ...


class InMemoryBrowserOperationResultStore:
    """Thread-safe immutable result store for local browser execution."""

    def __init__(self) -> None:
        self._outputs: dict[str, BrowserOperationOutput] = {}
        self._lock = RLock()

    def save(self, output: BrowserOperationOutput) -> None:
        if not isinstance(output, BrowserOperationOutput):
            raise ValueError("browser result store requires validated output")
        with self._lock:
            existing = self._outputs.get(output.operation_id)
            if existing is not None and existing != output:
                raise BrowserOperationConflictError(
                    "browser operation result cannot be rewritten"
                )
            self._outputs[output.operation_id] = output

    def get(self, operation_id: str) -> BrowserOperationOutput | None:
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("browser operation ID must not be blank")
        with self._lock:
            return self._outputs.get(operation_id.strip())


class BrowserCancellationToken:
    """Cooperative cancellation checked between expensive workflow phases."""

    def __init__(self) -> None:
        self._event = Event()

    @property
    def cancellation_requested(self) -> bool:
        return self._event.is_set()

    def request(self) -> None:
        self._event.set()

    def raise_if_requested(self) -> None:
        if self.cancellation_requested:
            raise BrowserOperationCancelled()


class BrowserOperationCancelled(Exception):
    """Private control-flow signal; never serialized to the browser."""


class BrowserOperationHandledFailure(Exception):
    """Carry an explicitly safe failure from a workflow boundary."""

    def __init__(self, failure: BrowserOperationFailure) -> None:
        if not isinstance(failure, BrowserOperationFailure):
            raise ValueError("handled browser failure must be validated")
        super().__init__(failure.message)
        self.failure = failure


@runtime_checkable
class BrowserOperationHandler(Protocol):
    def execute(
        self,
        request: BrowserOperationRequest,
        *,
        event_emitter: WorkflowEventEmitter,
        cancellation_token: BrowserCancellationToken,
    ) -> BrowserOperationOutput:
        ...


@runtime_checkable
class BrowserSwingResearchExecutor(Protocol):
    def execute(
        self,
        text: str,
        *,
        operation_id: str | None = None,
        event_emitter: WorkflowEventEmitter | None = None,
        manage_terminal_events: bool = True,
        instrument_resolved_callback: (
            Callable[[SwingAnalysisCommand], None] | None
        ) = None,
    ) -> JarvisSwingAnalysisResponse:
        ...


@runtime_checkable
class BrowserResearchPresenter(Protocol):
    def explain(
        self,
        result: (
            EndToEndSwingAnalysisResult
            | MultiTimeframeEndToEndSwingAnalysisResult
        ),
        *,
        user_name: str,
    ) -> JarvisResearchExplanation | JarvisMultiTimeframeResearchExplanation:
        ...


@runtime_checkable
class BrowserJudgeFollowUpExecutor(Protocol):
    def execute(
        self,
        question: str,
        *,
        technical_review: MultiTimeframeEvidenceReview,
        debate_result: AgenticDebateResult,
    ) -> JudgeFollowUpAnswer:
        ...


@runtime_checkable
class BrowserFundamentalEvidenceExecutor(Protocol):
    def resolve_issuer(
        self,
        request: FundamentalIssuerResolutionRequest,
        *,
        refresh_requested: bool = False,
    ) -> FundamentalIssuerLoadResult:
        ...

    def load(
        self,
        request,
        *,
        refresh_requested: bool = False,
    ) -> FundamentalEvidenceLoadResult:
        ...


@runtime_checkable
class BrowserResearchContextStore(Protocol):
    def replace(
        self,
        session_id: str,
        technical_review: MultiTimeframeEvidenceReview,
        debate_result: AgenticDebateResult,
    ) -> None:
        ...

    def clear(self, session_id: str) -> None:
        ...

    def get(
        self,
        session_id: str,
    ) -> tuple[MultiTimeframeEvidenceReview, AgenticDebateResult] | None:
        ...


class InMemoryBrowserResearchContextStore:
    """Retain only Judge-approved evidence needed by later session queries."""

    def __init__(self) -> None:
        self._contexts: dict[
            str,
            tuple[MultiTimeframeEvidenceReview, AgenticDebateResult],
        ] = {}
        self._lock = RLock()

    def replace(
        self,
        session_id: str,
        technical_review: MultiTimeframeEvidenceReview,
        debate_result: AgenticDebateResult,
    ) -> None:
        if not isinstance(technical_review, MultiTimeframeEvidenceReview):
            raise ValueError("browser context requires a technical review")
        if not isinstance(debate_result, AgenticDebateResult):
            raise ValueError("browser context requires a debate result")
        with self._lock:
            self._contexts[_required_text("session ID", session_id)] = (
                technical_review,
                debate_result,
            )

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._contexts.pop(_required_text("session ID", session_id), None)

    def get(
        self,
        session_id: str,
    ) -> tuple[MultiTimeframeEvidenceReview, AgenticDebateResult] | None:
        with self._lock:
            return self._contexts.get(_required_text("session ID", session_id))


class JarvisBrowserOperationHandler:
    """Run research, presentation, and grounded follow-ups under one contract."""

    def __init__(
        self,
        research_executor: BrowserSwingResearchExecutor,
        presenter: BrowserResearchPresenter,
        follow_up_executor: BrowserJudgeFollowUpExecutor,
        *,
        user_name: str,
        context_store: BrowserResearchContextStore | None = None,
        fundamental_evidence_executor: (
            BrowserFundamentalEvidenceExecutor | None
        ) = None,
        provider_scope_resolver: (
            Callable[[str], ProviderConnectionScope] | None
        ) = None,
        clock: RunnerClock | None = None,
    ) -> None:
        if not isinstance(research_executor, BrowserSwingResearchExecutor):
            raise ValueError("browser handler requires a research executor")
        if not isinstance(presenter, BrowserResearchPresenter):
            raise ValueError("browser handler requires a research presenter")
        if not isinstance(follow_up_executor, BrowserJudgeFollowUpExecutor):
            raise ValueError("browser handler requires a follow-up executor")
        resolved_context_store = (
            context_store
            if context_store is not None
            else InMemoryBrowserResearchContextStore()
        )
        if not isinstance(resolved_context_store, BrowserResearchContextStore):
            raise ValueError("browser handler requires a research context store")
        if (fundamental_evidence_executor is None) != (
            provider_scope_resolver is None
        ):
            raise ValueError(
                "browser handler fundamental execution requires executor "
                "and scope resolver"
            )
        if fundamental_evidence_executor is not None and not isinstance(
            fundamental_evidence_executor,
            BrowserFundamentalEvidenceExecutor,
        ):
            raise ValueError(
                "browser handler requires a fundamental evidence executor"
            )
        if provider_scope_resolver is not None and not callable(
            provider_scope_resolver
        ):
            raise ValueError("browser handler requires a provider scope resolver")
        self._research_executor = research_executor
        self._presenter = presenter
        self._follow_up_executor = follow_up_executor
        self._user_name = _required_text("user name", user_name)
        self._contexts = resolved_context_store
        self._fundamental_evidence = fundamental_evidence_executor
        self._provider_scope_resolver = provider_scope_resolver
        self._clock = clock or _ist_now

    def execute(
        self,
        request: BrowserOperationRequest,
        *,
        event_emitter: WorkflowEventEmitter,
        cancellation_token: BrowserCancellationToken,
    ) -> BrowserOperationOutput:
        if not isinstance(request, BrowserOperationRequest):
            raise ValueError("browser handler requires a validated request")
        if not isinstance(event_emitter, WorkflowEventEmitter):
            raise ValueError("browser handler requires an event emitter")
        if event_emitter.operation_id != request.operation_id:
            raise ValueError("browser handler emitter operation ID must match")
        if not isinstance(cancellation_token, BrowserCancellationToken):
            raise ValueError("browser handler requires a cancellation token")
        cancellation_token.raise_if_requested()
        if request.kind is BrowserOperationKind.SWING_ANALYSIS:
            return self._execute_analysis(
                request,
                event_emitter,
                cancellation_token,
            )
        return self._execute_follow_up(
            request,
            event_emitter,
            cancellation_token,
        )

    def _execute_analysis(
        self,
        request: BrowserOperationRequest,
        emitter: WorkflowEventEmitter,
        token: BrowserCancellationToken,
    ) -> BrowserOperationOutput:
        self._contexts.clear(request.session_id)
        fundamental_evidence: tuple[FundamentalEvidenceLoadResult, ...] = ()
        structured_document_references: tuple[
            BrowserStructuredDocumentReference,
            ...,
        ] = ()
        benchmarking_financials_reference: (
            BrowserBenchmarkingFinancialsReference | None
        ) = None
        fundamental_future: Future | None = None
        fundamental_pool = (
            ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="jarvis-fundamental",
            )
            if request.fundamentals_requested
            else None
        )

        def start_fundamental_analysis(command: SwingAnalysisCommand) -> None:
            nonlocal fundamental_future
            if fundamental_pool is None:
                return
            if fundamental_future is not None:
                raise RuntimeError("fundamental analysis was already started")
            fundamental_future = fundamental_pool.submit(
                self._load_fundamental_evidence,
                request,
                command,
                token,
            )

        try:
            try:
                response = self._research_executor.execute(
                    request.message,
                    operation_id=request.operation_id,
                    event_emitter=emitter,
                    manage_terminal_events=False,
                    instrument_resolved_callback=(
                        start_fundamental_analysis
                        if request.fundamentals_requested
                        else None
                    ),
                )
            except ApplicationError as exc:
                raise BrowserOperationHandledFailure(
                    _safe_research_failure(exc)
                ) from exc
            if fundamental_future is not None:
                (
                    fundamental_evidence,
                    structured_document_references,
                    benchmarking_financials_reference,
                ) = fundamental_future.result()
        finally:
            if fundamental_pool is not None:
                fundamental_pool.shutdown(wait=True)
        if not isinstance(response, JarvisSwingAnalysisResponse):
            raise ValueError("research executor returned an invalid response")
        if response.operation_id != request.operation_id:
            raise ValueError("research response operation ID must match")
        if response.status is JarvisCommandStatus.LLM_FAILURE:
            failure = response.failure
            if failure is None:
                raise ValueError("LLM failure response is missing its payload")
            raise BrowserOperationHandledFailure(
                BrowserOperationFailure(
                    code=f"llm.{failure.code.value}",
                    message=failure.display_message,
                    retryable=failure.retryable,
                )
            )
        if response.result is None:
            raise ValueError("completed research response is missing analysis")
        approved_context = None
        if (
            response.multi_timeframe_review is not None
            and response.multi_timeframe_debate is not None
        ):
            approved_context = (
                response.multi_timeframe_review,
                response.multi_timeframe_debate,
            )
        token.raise_if_requested()
        emitter.emit(WorkflowStage.PRESENTATION, WorkflowEventState.STARTED)
        try:
            explanation = self._presenter.explain(
                response.result,
                user_name=self._user_name,
            )
        except ApplicationError as exc:
            token.raise_if_requested()
            emitter.emit(WorkflowStage.PRESENTATION, WorkflowEventState.FAILED)
            if approved_context is not None:
                self._contexts.replace(request.session_id, *approved_context)
            return BrowserOperationOutput(
                operation_id=request.operation_id,
                session_id=request.session_id,
                kind=request.kind,
                completed_at=self._clock(),
                research_response=response,
                presentation_failure=BrowserOperationFailure(
                    code="presentation.unavailable",
                    message=(
                        "The analysis is complete, but Jarvis could not prepare "
                        "the narrated briefing. The validated result remains "
                        "available."
                    ),
                    retryable=(
                        isinstance(exc, LLMError) and exc.context.retryable
                    ),
                ),
                fundamental_evidence=fundamental_evidence,
                structured_document_references=(
                    structured_document_references
                ),
                benchmarking_financials_reference=(
                    benchmarking_financials_reference
                ),
            )
        if not isinstance(
            explanation,
            (JarvisResearchExplanation, JarvisMultiTimeframeResearchExplanation),
        ):
            raise ValueError("research presenter returned an invalid explanation")
        token.raise_if_requested()
        emitter.emit(WorkflowStage.PRESENTATION, WorkflowEventState.COMPLETED)
        if approved_context is not None:
            self._contexts.replace(request.session_id, *approved_context)
        return BrowserOperationOutput(
            operation_id=request.operation_id,
            session_id=request.session_id,
            kind=request.kind,
            completed_at=self._clock(),
            research_response=response,
            research_explanation=explanation,
            fundamental_evidence=fundamental_evidence,
            structured_document_references=structured_document_references,
            benchmarking_financials_reference=(
                benchmarking_financials_reference
            ),
        )

    def _load_fundamental_evidence(
        self,
        operation: BrowserOperationRequest,
        command: SwingAnalysisCommand,
        token: BrowserCancellationToken,
    ) -> tuple[
        tuple[FundamentalEvidenceLoadResult, ...],
        tuple[BrowserStructuredDocumentReference, ...],
        BrowserBenchmarkingFinancialsReference | None,
    ]:
        executor = self._fundamental_evidence
        resolver = self._provider_scope_resolver
        if executor is None or resolver is None:
            raise BrowserOperationHandledFailure(
                BrowserOperationFailure(
                    code="fundamentals.unconfigured",
                    message=(
                        "Fundamental research was requested, but no provider "
                        "connection is configured. The technical analysis "
                        "was not altered."
                    ),
                    retryable=False,
                )
            )
        token.raise_if_requested()
        try:
            connection = resolver(operation.session_id)
        except Exception as exc:
            raise BrowserOperationHandledFailure(
                BrowserOperationFailure(
                    code="fundamentals.connection_unavailable",
                    message=(
                        "Fundamental research was requested, but the provider "
                        "connection could not be resolved safely."
                    ),
                    retryable=False,
                )
            ) from exc
        if not isinstance(connection, ProviderConnectionScope):
            raise ValueError("provider scope resolver returned an invalid scope")

        requested_at = self._clock()
        resolution_request = FundamentalIssuerResolutionRequest(
            request_id=f"{operation.operation_id}.fundamental.resolve",
            operation_id=operation.operation_id,
            connection=connection,
            requested_at=requested_at,
            locator=FundamentalIssuerLocator(
                exchange=command.exchange,
                symbol=_fundamental_provider_symbol(
                    command.exchange,
                    command.symbol,
                ),
            ),
        )
        try:
            resolution = executor.resolve_issuer(
                resolution_request,
                refresh_requested=operation.refresh_requested,
            )
        except FundamentalGatewayError as exc:
            raise BrowserOperationHandledFailure(
                BrowserOperationFailure(
                    code="fundamentals.unavailable",
                    message=(
                        "Fundamental research could not resolve the provider "
                        "company identity safely. No identity was guessed."
                    ),
                    retryable=exc.context.retryable,
                )
            ) from exc
        if not isinstance(resolution, FundamentalIssuerLoadResult):
            raise ValueError("fundamental executor returned invalid issuer data")
        if resolution.issuer is None:
            raise BrowserOperationHandledFailure(
                BrowserOperationFailure(
                    code="fundamentals.issuer_unresolved",
                    message=(
                        "Fundamental research was requested, but the provider "
                        "could not unambiguously match the resolved NSE "
                        "instrument. No company identity was guessed."
                    ),
                    retryable=False,
                )
            )
        issuer = resolution.issuer
        # Tijori supplies current provider-standardized evidence and does not
        # establish historical point-in-time availability.  The latest market
        # candle may legitimately predate today on weekends or holidays, so it
        # must not be reused as a fundamental evidence cutoff.
        as_of_date = None
        requests = (
            FundamentalCompanyOverviewRequest(
                request_id=f"{operation.operation_id}.fundamental.overview",
                operation_id=operation.operation_id,
                connection=connection,
                requested_at=requested_at,
                issuer=issuer,
                as_of_date=as_of_date,
            ),
            FundamentalFinancialsRequest(
                request_id=f"{operation.operation_id}.fundamental.financials",
                operation_id=operation.operation_id,
                connection=connection,
                requested_at=requested_at,
                issuer=issuer,
                as_of_date=as_of_date,
            ),
            FundamentalShareholdingRequest(
                request_id=f"{operation.operation_id}.fundamental.shareholding",
                operation_id=operation.operation_id,
                connection=connection,
                requested_at=requested_at,
                issuer=issuer,
                as_of_date=as_of_date,
            ),
        )
        loaded = []
        structured_references = []
        benchmarking_reference = None
        try:
            for request in requests:
                token.raise_if_requested()
                evidence = executor.load(
                    request,
                    refresh_requested=operation.refresh_requested,
                )
                if not isinstance(evidence, FundamentalEvidenceLoadResult):
                    raise ValueError(
                        "fundamental executor returned an invalid result"
                    )
                loaded.append(evidence)
            structured_requests = (
                FundamentalStructuredDocumentRequest(
                    request_id=(
                        f"{operation.operation_id}.fundamental.growth_table"
                    ),
                    operation_id=operation.operation_id,
                    connection=connection,
                    requested_at=requested_at,
                    issuer=issuer,
                    as_of_date=as_of_date,
                    document_type=FinancialDocumentType.GROWTH_TABLE,
                    reporting_basis=FinancialReportingBasis.NOT_APPLICABLE,
                ),
                FundamentalStructuredDocumentRequest(
                    request_id=(
                        f"{operation.operation_id}.fundamental."
                        "balance_sheet.consolidated"
                    ),
                    operation_id=operation.operation_id,
                    connection=connection,
                    requested_at=requested_at,
                    issuer=issuer,
                    as_of_date=as_of_date,
                    document_type=FinancialDocumentType.BALANCE_SHEET,
                    reporting_basis=FinancialReportingBasis.CONSOLIDATED,
                ),
                FundamentalStructuredDocumentRequest(
                    request_id=(
                        f"{operation.operation_id}.fundamental."
                        "balance_sheet.standalone"
                    ),
                    operation_id=operation.operation_id,
                    connection=connection,
                    requested_at=requested_at,
                    issuer=issuer,
                    as_of_date=as_of_date,
                    document_type=FinancialDocumentType.BALANCE_SHEET,
                    reporting_basis=FinancialReportingBasis.STANDALONE,
                ),
                FundamentalStructuredDocumentRequest(
                    request_id=(
                        f"{operation.operation_id}.fundamental."
                        "profit_and_loss.consolidated"
                    ),
                    operation_id=operation.operation_id,
                    connection=connection,
                    requested_at=requested_at,
                    issuer=issuer,
                    as_of_date=as_of_date,
                    document_type=FinancialDocumentType.PROFIT_AND_LOSS,
                    reporting_basis=FinancialReportingBasis.CONSOLIDATED,
                ),
                FundamentalStructuredDocumentRequest(
                    request_id=(
                        f"{operation.operation_id}.fundamental."
                        "profit_and_loss.standalone"
                    ),
                    operation_id=operation.operation_id,
                    connection=connection,
                    requested_at=requested_at,
                    issuer=issuer,
                    as_of_date=as_of_date,
                    document_type=FinancialDocumentType.PROFIT_AND_LOSS,
                    reporting_basis=FinancialReportingBasis.STANDALONE,
                ),
                FundamentalStructuredDocumentRequest(
                    request_id=(
                        f"{operation.operation_id}.fundamental."
                        "cash_flow.consolidated"
                    ),
                    operation_id=operation.operation_id,
                    connection=connection,
                    requested_at=requested_at,
                    issuer=issuer,
                    as_of_date=as_of_date,
                    document_type=FinancialDocumentType.CASH_FLOW,
                    reporting_basis=FinancialReportingBasis.CONSOLIDATED,
                ),
                FundamentalStructuredDocumentRequest(
                    request_id=(
                        f"{operation.operation_id}.fundamental."
                        "cash_flow.standalone"
                    ),
                    operation_id=operation.operation_id,
                    connection=connection,
                    requested_at=requested_at,
                    issuer=issuer,
                    as_of_date=as_of_date,
                    document_type=FinancialDocumentType.CASH_FLOW,
                    reporting_basis=FinancialReportingBasis.STANDALONE,
                ),
                FundamentalStructuredDocumentRequest(
                    request_id=(
                        f"{operation.operation_id}.fundamental."
                        "ratios.consolidated"
                    ),
                    operation_id=operation.operation_id,
                    connection=connection,
                    requested_at=requested_at,
                    issuer=issuer,
                    as_of_date=as_of_date,
                    document_type=FinancialDocumentType.RATIOS,
                    reporting_basis=FinancialReportingBasis.CONSOLIDATED,
                ),
                FundamentalStructuredDocumentRequest(
                    request_id=(
                        f"{operation.operation_id}.fundamental."
                        "ratios.standalone"
                    ),
                    operation_id=operation.operation_id,
                    connection=connection,
                    requested_at=requested_at,
                    issuer=issuer,
                    as_of_date=as_of_date,
                    document_type=FinancialDocumentType.RATIOS,
                    reporting_basis=FinancialReportingBasis.STANDALONE,
                ),
                FundamentalStructuredDocumentRequest(
                    request_id=(
                        f"{operation.operation_id}.fundamental."
                        "quarterly_results.consolidated"
                    ),
                    operation_id=operation.operation_id,
                    connection=connection,
                    requested_at=requested_at,
                    issuer=issuer,
                    as_of_date=as_of_date,
                    document_type=FinancialDocumentType.QUARTERLY_RESULTS,
                    reporting_basis=FinancialReportingBasis.CONSOLIDATED,
                ),
                FundamentalStructuredDocumentRequest(
                    request_id=(
                        f"{operation.operation_id}.fundamental."
                        "quarterly_results.standalone"
                    ),
                    operation_id=operation.operation_id,
                    connection=connection,
                    requested_at=requested_at,
                    issuer=issuer,
                    as_of_date=as_of_date,
                    document_type=FinancialDocumentType.QUARTERLY_RESULTS,
                    reporting_basis=FinancialReportingBasis.STANDALONE,
                ),
            )
            for structured_request in structured_requests:
                token.raise_if_requested()
                structured_document = executor.load_structured_document(
                    structured_request,
                    refresh_requested=operation.refresh_requested,
                )
                if not isinstance(
                    structured_document,
                    StructuredDocumentLoadResult,
                ):
                    raise ValueError(
                        "fundamental executor returned an invalid structured "
                        "document result"
                    )
                stored_document = structured_document.stored_document
                if stored_document is None:
                    continue
                document = stored_document.result.document
                if document is None:
                    raise ValueError(
                        "stored structured result is missing its document"
                    )
                structured_references.append(
                    BrowserStructuredDocumentReference(
                        cache_entry_id=(
                            stored_document.cache_key.cache_entry_id
                        ),
                        document_id=document.document_id,
                        document_type=document.document_type,
                        reporting_basis=document.reporting_basis,
                        exchange=document.issuer.exchange,
                        symbol=document.issuer.symbol,
                        source=structured_document.source,
                        document_fingerprint=(
                            stored_document.document_fingerprint
                        ),
                        retrieved_at=stored_document.retrieved_at,
                        stored_at=stored_document.stored_at,
                        expires_at=stored_document.expires_at,
                        all_sections_expanded=(
                            document.all_sections_expanded
                        ),
                    )
                )
            token.raise_if_requested()
            benchmarking = executor.load_benchmarking_financials(
                requests[0],
                refresh_requested=operation.refresh_requested,
            )
            if not isinstance(
                benchmarking,
                BenchmarkingFinancialsLoadResult,
            ):
                raise ValueError(
                    "fundamental executor returned an invalid Benchmarking "
                    "Financials result"
                )
            stored_benchmarking = benchmarking.stored_document
            if stored_benchmarking is not None:
                document = stored_benchmarking.result.document
                if document is None:
                    raise ValueError(
                        "stored Benchmarking Financials result is missing "
                        "its document"
                    )
                benchmarking_reference = (
                    BrowserBenchmarkingFinancialsReference(
                        cache_entry_id=(
                            stored_benchmarking.cache_key.cache_entry_id
                        ),
                        document_id=document.document_id,
                        exchange=document.issuer.exchange,
                        symbol=document.issuer.symbol,
                        source=benchmarking.source,
                        document_fingerprint=(
                            stored_benchmarking.document_fingerprint
                        ),
                        observation_date=document.observation_date,
                        retrieved_at=stored_benchmarking.retrieved_at,
                        stored_at=stored_benchmarking.stored_at,
                        expires_at=stored_benchmarking.expires_at,
                        all_rows_captured=document.all_rows_captured,
                        company_count=len(document.companies),
                        row_count=len(document.rows),
                    )
                )
        except FundamentalGatewayError as exc:
            raise BrowserOperationHandledFailure(
                BrowserOperationFailure(
                    code="fundamentals.unavailable",
                    message=(
                        "Fundamental research could not be retrieved safely. "
                        "No provider facts were invented or substituted."
                    ),
                    retryable=exc.context.retryable,
                )
            ) from exc
        return (
            tuple(loaded),
            tuple(structured_references),
            benchmarking_reference,
        )

    def _execute_follow_up(
        self,
        request: BrowserOperationRequest,
        emitter: WorkflowEventEmitter,
        token: BrowserCancellationToken,
    ) -> BrowserOperationOutput:
        context = self._contexts.get(request.session_id)
        if context is None:
            raise BrowserOperationHandledFailure(
                BrowserOperationFailure(
                    code="conversation.context_unavailable",
                    message=(
                        "I need a completed multi-timeframe analysis before "
                        "the Judge can answer that follow-up."
                    ),
                    retryable=False,
                )
            )
        emitter.emit(WorkflowStage.FOLLOW_UP, WorkflowEventState.STARTED)
        token.raise_if_requested()
        try:
            answer = self._follow_up_executor.execute(
                request.message,
                technical_review=context[0],
                debate_result=context[1],
            )
        except ApplicationError as exc:
            emitter.emit(WorkflowStage.FOLLOW_UP, WorkflowEventState.FAILED)
            raise BrowserOperationHandledFailure(
                BrowserOperationFailure(
                    code="follow_up.unavailable",
                    message=(
                        "The Judge could not answer that grounded follow-up "
                        "just now. The original analysis is unchanged."
                    ),
                    retryable=(
                        isinstance(exc, LLMError) and exc.context.retryable
                    ),
                )
            )
        if not isinstance(answer, JudgeFollowUpAnswer):
            raise ValueError("follow-up executor returned an invalid answer")
        token.raise_if_requested()
        emitter.emit(WorkflowStage.FOLLOW_UP, WorkflowEventState.COMPLETED)
        return BrowserOperationOutput(
            operation_id=request.operation_id,
            session_id=request.session_id,
            kind=request.kind,
            completed_at=self._clock(),
            judge_follow_up=answer,
        )


class AsyncBrowserOperationRunner:
    """Bounded background runner behind future HTTP/WebSocket adapters."""

    def __init__(
        self,
        registry: BrowserOperationRegistry,
        handler: BrowserOperationHandler,
        result_store: BrowserOperationResultStore,
        *,
        max_workers: int = 2,
        clock: RunnerClock | None = None,
    ) -> None:
        if not isinstance(registry, BrowserOperationRegistry):
            raise ValueError("browser runner requires an operation registry")
        if not isinstance(handler, BrowserOperationHandler):
            raise ValueError("browser runner requires an operation handler")
        if not isinstance(result_store, BrowserOperationResultStore):
            raise ValueError("browser runner requires a result store")
        if not isinstance(max_workers, int) or isinstance(max_workers, bool):
            raise ValueError("browser runner worker count must be an integer")
        if max_workers < 1 or max_workers > 16:
            raise ValueError("browser runner worker count must be between 1 and 16")
        self._registry = registry
        self._handler = handler
        self._results = result_store
        self._clock = clock or _ist_now
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="jarvis-browser",
        )
        self._futures: dict[str, Future[None]] = {}
        self._tokens: dict[str, BrowserCancellationToken] = {}
        self._lock = RLock()

    def submit(self, request: BrowserOperationRequest) -> BrowserOperationSnapshot:
        snapshot = self._registry.submit(request)
        with self._lock:
            if (
                snapshot.status is BrowserOperationStatus.QUEUED
                and snapshot.request.operation_id not in self._futures
            ):
                operation_id = snapshot.request.operation_id
                token = BrowserCancellationToken()
                self._tokens[operation_id] = token
                self._futures[operation_id] = self._executor.submit(
                    self._run,
                    operation_id,
                    token,
                )
        return snapshot

    def open_session(
        self,
        session: BrowserSessionSnapshot,
    ) -> BrowserSessionSnapshot:
        return self._registry.open_session(session)

    def get_session(self, session_id: str) -> BrowserSessionSnapshot | None:
        return self._registry.get_session(session_id)

    def close_session(
        self,
        session_id: str,
        *,
        closed_at: datetime,
    ) -> BrowserSessionSnapshot:
        return self._registry.close_session(session_id, closed_at=closed_at)

    def cancel(self, operation_id: str) -> BrowserOperationSnapshot:
        snapshot = self._registry.request_cancellation(
            operation_id,
            at=self._clock(),
        )
        with self._lock:
            token = self._tokens.get(operation_id)
            future = self._futures.get(operation_id)
            if token is not None:
                token.request()
            if (
                future is not None
                and snapshot.status is BrowserOperationStatus.CANCELLED
            ):
                future.cancel()
        return snapshot

    def get_operation(self, operation_id: str) -> BrowserOperationSnapshot | None:
        return self._registry.get_operation(operation_id)

    def get_result(self, operation_id: str) -> BrowserOperationOutput | None:
        return self._results.get(operation_id)

    def replay(self, cursor: WorkflowEventReplayCursor) -> WorkflowEventBatch:
        return self._registry.replay(cursor)

    def shutdown(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=not wait)

    def _run(
        self,
        operation_id: str,
        token: BrowserCancellationToken,
    ) -> None:
        try:
            snapshot = self._registry.get_operation(operation_id)
            if snapshot is None or snapshot.status is not BrowserOperationStatus.QUEUED:
                return
            self._registry.mark_running(operation_id, at=self._clock())
            token.raise_if_requested()
            emitter = WorkflowEventEmitter(
                operation_id,
                self._registry,
                clock=self._clock,
            )
            output = self._handler.execute(
                snapshot.request,
                event_emitter=emitter,
                cancellation_token=token,
            )
            if not isinstance(output, BrowserOperationOutput):
                raise ValueError("browser handler returned an invalid output")
            if (
                output.operation_id != operation_id
                or output.session_id != snapshot.request.session_id
                or output.kind is not snapshot.request.kind
            ):
                raise ValueError("browser handler output identity must match request")
            token.raise_if_requested()
            self._results.save(output)
            emitter.emit(WorkflowStage.COMPLETED, WorkflowEventState.COMPLETED)
            self._registry.mark_completed(operation_id, at=self._clock())
            logger.info(
                "Browser operation completed",
                extra={
                    "event": "jarvis.browser_operation.completed",
                    "browser_operation_id": operation_id,
                },
            )
        except BrowserOperationCancelled:
            current = self._registry.get_operation(operation_id)
            if (
                current is not None
                and current.status is BrowserOperationStatus.CANCELLATION_REQUESTED
            ):
                self._registry.mark_cancelled(operation_id, at=self._clock())
                logger.info(
                    "Browser operation cancelled",
                    extra={
                        "event": "jarvis.browser_operation.cancelled",
                        "browser_operation_id": operation_id,
                    },
                )
        except BrowserOperationHandledFailure as exc:
            logger.warning(
                "Browser operation stopped with a safe failure",
                extra={
                    "event": "jarvis.browser_operation.failed",
                    "browser_operation_id": operation_id,
                    "failure_code": exc.failure.code,
                    "retryable": exc.failure.retryable,
                },
            )
            self._fail(operation_id, exc.failure)
        except Exception as exc:
            logger.error(
                "Browser operation stopped unexpectedly",
                extra={
                    "event": "jarvis.browser_operation.unexpected_failure",
                    "browser_operation_id": operation_id,
                    "error_type": type(exc).__name__,
                },
            )
            self._fail(
                operation_id,
                BrowserOperationFailure(
                    code="workflow.unexpected",
                    message=(
                        "Jarvis encountered an unexpected workflow error. "
                        "No unverified conclusion was produced."
                    ),
                    retryable=False,
                ),
            )
        finally:
            with self._lock:
                self._tokens.pop(operation_id, None)

    def _fail(
        self,
        operation_id: str,
        failure: BrowserOperationFailure,
    ) -> None:
        current = self._registry.get_operation(operation_id)
        if current is None or current.status.terminal:
            return
        emitter = WorkflowEventEmitter(
            operation_id,
            self._registry,
            clock=self._clock,
            initial_sequence=current.last_event_sequence,
        )
        emitter.emit(WorkflowStage.FAILED, WorkflowEventState.FAILED)
        self._registry.mark_failed(operation_id, failure, at=self._clock())


def _required_text(label: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"browser {label} must not be blank")
    return value.strip()


def _fundamental_provider_symbol(exchange: str, symbol: str) -> str:
    """Translate only Angel One's NSE cash-series suffix for provider lookup."""

    normalized_exchange = _required_text("exchange", exchange).upper()
    normalized_symbol = _required_text("symbol", symbol).upper()
    if normalized_exchange == "NSE" and normalized_symbol.endswith("-EQ"):
        return normalized_symbol[:-3]
    return normalized_symbol


def _safe_research_failure(exc: ApplicationError) -> BrowserOperationFailure:
    """Translate expected research failures without exposing provider details."""
    if isinstance(exc, IntentRecognitionError):
        return BrowserOperationFailure(
            code="request.intent_unrecognized",
            message=(
                "I could not identify one company to analyse. Try, for "
                "example, 'Analyse TCS for me'."
            ),
            retryable=False,
        )
    if isinstance(exc, InstrumentNotFoundError):
        return BrowserOperationFailure(
            code="instrument.not_found",
            message=(
                "I could not match that company to one NSE cash-market "
                "instrument. Please use its company name or trading symbol."
            ),
            retryable=False,
        )
    if isinstance(exc, AmbiguousInstrumentError):
        return BrowserOperationFailure(
            code="instrument.ambiguous",
            message=(
                "That name matches more than one instrument. Please use the "
                "exact NSE trading symbol."
            ),
            retryable=False,
        )
    if isinstance(exc, InstrumentResolutionError):
        return BrowserOperationFailure(
            code="instrument.catalog_unavailable",
            message=(
                "The NSE instrument catalogue is unavailable just now. "
                "Jarvis will not guess the symbol token."
            ),
            retryable=isinstance(exc, InstrumentMasterDownloadError),
        )
    if isinstance(exc, MarketDataError):
        return BrowserOperationFailure(
            code="market.unavailable",
            message=(
                "Angel One could not provide the required market history. "
                "No analysis was fabricated; please try again shortly."
            ),
            retryable=True,
        )
    return BrowserOperationFailure(
        code="workflow.unavailable",
        message=(
            "Jarvis could not complete the validated research workflow. "
            "No unverified conclusion was produced."
        ),
        retryable=False,
    )


def _ist_now() -> datetime:
    return datetime.now(IST)
