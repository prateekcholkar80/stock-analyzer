import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime
from hashlib import sha256
from re import fullmatch
from typing import Protocol, runtime_checkable
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.models import (
    BrowserBenchmarkingFinancialsDocument,
    BrowserBenchmarkingFinancialsDocumentResponse,
    BrowserConversationInputRequest,
    BrowserOperationResultResponse,
    BrowserStructuredFinancialDocument,
    BrowserStructuredFinancialDocumentResponse,
    CreateBrowserSessionResponse,
    ProviderSessionLifecycleResponse,
    ProviderSessionStatusRequest,
    ProviderSessionTargetRequest,
    ProvisionProviderSessionRequest,
    RevokeProviderSessionRequest,
    SpeechSynthesisRequest,
    SpeechTranscriptionResponse,
    SubmitBrowserOperationRequest,
)
from app.api.session_auth import (
    BrowserSessionAuthorizer,
    InMemoryBrowserSessionAuthorizer,
)
from app.audit.prompt_audit import prompt_audit_session_context
from app.fundamentals.session_provisioning import (
    ProviderSessionInspectionRequest,
    ProviderSessionLifecycle,
    ProviderSessionProvisioningRequest,
    ProviderSessionRevocationRequest,
)
from app.exceptions import (
    BrowserOperationConflictError,
    BrowserOperationNotFoundError,
    BrowserSessionNotFoundError,
    STTAuthenticationError,
    STTConfigurationError,
    STTError,
    STTProviderUnavailableError,
    STTTranscriptionError,
    TTSAuthenticationError,
    TTSConfigurationError,
    TTSError,
    TTSProviderUnavailableError,
    TTSSynthesisError,
    StorageError,
)
from app.models.browser_operations import (
    BrowserOperationOutput,
    BrowserOperationRequest,
    BrowserOperationSnapshot,
    BrowserOperationStatus,
    BrowserSessionSnapshot,
    WorkflowEventBatch,
    WorkflowEventReplayCursor,
)
from app.models.browser_conversation import (
    BrowserConversationSnapshot,
    BrowserConversationTurn,
    ConversationEventBatch,
    ConversationEventReplayCursor,
)
from app.models.conversation import JarvisUtterance
from app.models.dashboard import JarvisDashboardView
from app.models.fundamentals import ProviderConnectionScope
from app.models.financial_document_storage import (
    StructuredDocumentRepositoryScope,
)
from app.presentation.dashboard import (
    DashboardProjectionError,
    JarvisDashboardProjector,
)
from app.stt.gateway import Transcription
from app.services.provider_sessions import ProviderSessionCommandError
from app.storage.financial_document_repositories import (
    StructuredFinancialDocumentRepository,
)
from app.storage.benchmarking_financials_repositories import (
    BenchmarkingFinancialsRepository,
)
from app.tts.gateway import SpeechSynthesis


IST = ZoneInfo("Asia/Kolkata")
ApiClock = Callable[[], datetime]
IdFactory = Callable[[], str]
_MAX_TRANSCRIPTION_AUDIO_BYTES = 10 * 1024 * 1024


@runtime_checkable
class BrowserOperationApplication(Protocol):
    def open_session(
        self,
        session: BrowserSessionSnapshot,
    ) -> BrowserSessionSnapshot:
        ...

    def get_session(self, session_id: str) -> BrowserSessionSnapshot | None:
        ...

    def close_session(
        self,
        session_id: str,
        *,
        closed_at: datetime,
    ) -> BrowserSessionSnapshot:
        ...

    def submit(
        self,
        request: BrowserOperationRequest,
    ) -> BrowserOperationSnapshot:
        ...

    def cancel(self, operation_id: str) -> BrowserOperationSnapshot:
        ...

    def get_operation(
        self,
        operation_id: str,
    ) -> BrowserOperationSnapshot | None:
        ...

    def get_result(self, operation_id: str) -> BrowserOperationOutput | None:
        ...

    def replay(self, cursor: WorkflowEventReplayCursor) -> WorkflowEventBatch:
        ...


@runtime_checkable
class BrowserConversationApplication(Protocol):
    def open_session(
        self,
        session_id: str,
        *,
        at: datetime,
    ) -> BrowserConversationSnapshot:
        ...

    def close_session(self, session_id: str, *, at: datetime) -> None:
        ...

    def get_snapshot(
        self,
        session_id: str,
        *,
        at: datetime,
    ) -> BrowserConversationSnapshot:
        ...

    def handle(
        self,
        session_id: str,
        utterance: JarvisUtterance,
        *,
        idempotency_key: str,
        operation_id_factory: IdFactory,
        at: datetime,
    ) -> BrowserConversationTurn:
        ...

    def sleep(
        self,
        session_id: str,
        *,
        at: datetime,
    ) -> BrowserConversationSnapshot:
        ...

    def replay(
        self,
        cursor: ConversationEventReplayCursor,
    ) -> ConversationEventBatch:
        ...


@runtime_checkable
class SpeechSynthesisApplication(Protocol):
    """Stateless text-to-speech capability -- not tied to any turn ID."""

    def synthesize(self, *, text: str) -> SpeechSynthesis:
        ...


@runtime_checkable
class TranscriptionApplication(Protocol):
    """Stateless speech-to-text capability -- not tied to any turn ID."""

    def transcribe(self, *, audio: bytes, media_type: str) -> Transcription:
        ...


@runtime_checkable
class ProviderSessionApplication(Protocol):
    def status(
        self,
        *,
        request: ProviderSessionInspectionRequest,
    ) -> ProviderSessionLifecycle:
        ...

    def provision(
        self,
        *,
        request: ProviderSessionProvisioningRequest,
    ) -> ProviderSessionLifecycle:
        ...

    def revoke(
        self,
        *,
        request: ProviderSessionRevocationRequest,
    ) -> ProviderSessionLifecycle:
        ...


@runtime_checkable
class BrowserSessionOwnershipRegistry(Protocol):
    def bind_browser_session(
        self,
        *,
        browser_session_id: str,
        tenant_id: str,
    ) -> None:
        ...

    def unbind_browser_session(self, browser_session_id: str) -> bool:
        ...


def create_jarvis_http_app(
    operations: BrowserOperationApplication,
    *,
    conversation: BrowserConversationApplication | None = None,
    speech: SpeechSynthesisApplication | None = None,
    transcription: TranscriptionApplication | None = None,
    provider_sessions: ProviderSessionApplication | None = None,
    provider_connection_scope_resolver: Callable[
        [str, ProviderSessionTargetRequest],
        ProviderConnectionScope,
    ]
    | None = None,
    browser_session_ownership: BrowserSessionOwnershipRegistry | None = None,
    tenant_identity_resolver: Callable[[Request], str] | None = None,
    structured_document_repository: (
        StructuredFinancialDocumentRepository | None
    ) = None,
    benchmarking_financials_repository: (
        BenchmarkingFinancialsRepository | None
    ) = None,
    structured_document_scope_resolver: (
        Callable[[str], ProviderConnectionScope] | None
    ) = None,
    dashboard_projector: JarvisDashboardProjector | None = None,
    authorizer: BrowserSessionAuthorizer | None = None,
    clock: ApiClock | None = None,
    session_id_factory: IdFactory | None = None,
    operation_id_factory: IdFactory | None = None,
    shutdown: Callable[[], None] | None = None,
    allowed_origins: tuple[str, ...] = (),
    sse_poll_interval_seconds: float = 0.25,
    sse_heartbeat_seconds: float = 15.0,
) -> FastAPI:
    """Create the versioned HTTP adapter without composing financial services."""

    if not isinstance(operations, BrowserOperationApplication):
        raise ValueError("Jarvis HTTP API requires an operation application")
    if conversation is not None and not isinstance(
        conversation,
        BrowserConversationApplication,
    ):
        raise ValueError("Jarvis HTTP API requires a conversation application")
    if speech is not None and not isinstance(
        speech,
        SpeechSynthesisApplication,
    ):
        raise ValueError("Jarvis HTTP API requires a speech synthesis application")
    if transcription is not None and not isinstance(
        transcription,
        TranscriptionApplication,
    ):
        raise ValueError("Jarvis HTTP API requires a transcription application")
    if (provider_sessions is None) != (
        provider_connection_scope_resolver is None
    ):
        raise ValueError(
            "Jarvis HTTP API provider sessions require service and scope resolver"
        )
    if provider_sessions is not None and not isinstance(
        provider_sessions,
        ProviderSessionApplication,
    ):
        raise ValueError("Jarvis HTTP API requires a provider session application")
    if (
        provider_connection_scope_resolver is not None
        and not callable(provider_connection_scope_resolver)
    ):
        raise ValueError("Jarvis HTTP API requires a provider scope resolver")
    if (browser_session_ownership is None) != (
        tenant_identity_resolver is None
    ):
        raise ValueError(
            "Jarvis HTTP API session ownership requires registry and tenant resolver"
        )
    if browser_session_ownership is not None and not isinstance(
        browser_session_ownership,
        BrowserSessionOwnershipRegistry,
    ):
        raise ValueError("Jarvis HTTP API requires a session ownership registry")
    if tenant_identity_resolver is not None and not callable(
        tenant_identity_resolver
    ):
        raise ValueError("Jarvis HTTP API requires a tenant identity resolver")
    document_repositories_configured = any(
        repository is not None
        for repository in (
            structured_document_repository,
            benchmarking_financials_repository,
        )
    )
    if document_repositories_configured != (
        structured_document_scope_resolver is not None
    ):
        raise ValueError(
            "Jarvis HTTP API document reads require repositories and a scope "
            "resolver"
        )
    if structured_document_repository is not None and not isinstance(
        structured_document_repository,
        StructuredFinancialDocumentRepository,
    ):
        raise ValueError(
            "Jarvis HTTP API requires structured document storage"
        )
    if (
        benchmarking_financials_repository is not None
        and not isinstance(
            benchmarking_financials_repository,
            BenchmarkingFinancialsRepository,
        )
    ):
        raise ValueError(
            "Jarvis HTTP API requires Benchmarking Financials storage"
        )
    if (
        structured_document_scope_resolver is not None
        and not callable(structured_document_scope_resolver)
    ):
        raise ValueError(
            "Jarvis HTTP API requires a structured document scope resolver"
        )
    resolved_authorizer = authorizer or InMemoryBrowserSessionAuthorizer()
    resolved_dashboard_projector = (
        dashboard_projector or JarvisDashboardProjector()
    )
    if not isinstance(resolved_authorizer, BrowserSessionAuthorizer):
        raise ValueError("Jarvis HTTP API requires a session authorizer")
    resolved_clock = clock or _ist_now
    resolved_session_ids = session_id_factory or (
        lambda: f"session-{uuid4().hex}"
    )
    resolved_operation_ids = operation_id_factory or (
        lambda: f"operation-{uuid4().hex}"
    )
    for label, dependency in (
        ("clock", resolved_clock),
        ("session ID factory", resolved_session_ids),
        ("operation ID factory", resolved_operation_ids),
    ):
        if not callable(dependency):
            raise ValueError(f"Jarvis HTTP API {label} must be callable")
    if shutdown is not None and not callable(shutdown):
        raise ValueError("Jarvis HTTP API shutdown hook must be callable")
    if not isinstance(allowed_origins, tuple) or any(
        not isinstance(origin, str) or not origin.strip()
        for origin in allowed_origins
    ):
        raise ValueError("Jarvis HTTP API origins must be non-blank strings")
    if (
        not isinstance(sse_poll_interval_seconds, (int, float))
        or isinstance(sse_poll_interval_seconds, bool)
        or not 0.05 <= sse_poll_interval_seconds <= 5.0
    ):
        raise ValueError(
            "Jarvis SSE polling interval must be between 0.05 and 5 seconds"
        )
    if (
        not isinstance(sse_heartbeat_seconds, (int, float))
        or isinstance(sse_heartbeat_seconds, bool)
        or not 1.0 <= sse_heartbeat_seconds <= 60.0
    ):
        raise ValueError(
            "Jarvis SSE heartbeat must be between 1 and 60 seconds"
        )

    @asynccontextmanager
    async def lifespan(_app):
        yield
        if shutdown is not None:
            shutdown()

    app = FastAPI(
        title="Jarvis Investment Research API",
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[origin.strip() for origin in allowed_origins],
            allow_credentials=False,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "X-Jarvis-Session-Token"],
        )

    @app.exception_handler(BrowserSessionNotFoundError)
    async def session_not_found(_request, exc):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc)},
        )

    @app.exception_handler(BrowserOperationNotFoundError)
    async def operation_not_found(_request, exc):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc)},
        )

    @app.exception_handler(BrowserOperationConflictError)
    async def operation_conflict(_request, exc):
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc)},
        )

    @app.exception_handler(TTSError)
    async def tts_failure(_request, exc):
        if isinstance(exc, TTSConfigurationError):
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        elif isinstance(
            exc,
            (
                TTSProviderUnavailableError,
                TTSAuthenticationError,
                TTSSynthesisError,
            ),
        ):
            status_code = status.HTTP_502_BAD_GATEWAY
        else:
            status_code = status.HTTP_502_BAD_GATEWAY
        return JSONResponse(
            status_code=status_code,
            content={"detail": "Speech synthesis is currently unavailable."},
        )

    @app.exception_handler(STTError)
    async def stt_failure(_request, exc):
        if isinstance(exc, STTConfigurationError):
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        elif isinstance(
            exc,
            (
                STTProviderUnavailableError,
                STTAuthenticationError,
                STTTranscriptionError,
            ),
        ):
            status_code = status.HTTP_502_BAD_GATEWAY
        else:
            status_code = status.HTTP_502_BAD_GATEWAY
        return JSONResponse(
            status_code=status_code,
            content={"detail": "Speech transcription is currently unavailable."},
        )

    @app.exception_handler(ProviderSessionCommandError)
    async def provider_session_failure(_request, _exc):
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"detail": "Provider session command is currently unavailable."},
        )

    def authorize(session_id: str, access_token: str | None) -> None:
        if access_token is None or not access_token.strip():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="browser session token is required",
            )
        try:
            resolved_authorizer.authorize(session_id, access_token)
        except (BrowserSessionNotFoundError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="browser session authorization failed",
            ) from exc

    def resolve_tenant_identity(request: Request) -> str | None:
        if tenant_identity_resolver is None:
            return None
        try:
            tenant_id = tenant_identity_resolver(request)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="tenant identity could not be established",
            ) from exc
        if (
            not isinstance(tenant_id, str)
            or len(tenant_id) > 128
            or fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", tenant_id) is None
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="tenant identity could not be established",
            )
        return tenant_id

    def bind_browser_ownership(session_id: str, tenant_id: str) -> None:
        if browser_session_ownership is None:
            return
        try:
            browser_session_ownership.bind_browser_session(
                browser_session_id=session_id,
                tenant_id=tenant_id,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="browser session ownership is currently unavailable",
            ) from exc

    def unbind_browser_ownership(session_id: str) -> None:
        if browser_session_ownership is None:
            return
        try:
            browser_session_ownership.unbind_browser_session(session_id)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="browser session ownership is currently unavailable",
            ) from exc

    def owned_operation(
        session_id: str,
        operation_id: str,
    ) -> BrowserOperationSnapshot:
        snapshot = operations.get_operation(operation_id)
        if snapshot is None or snapshot.request.session_id != session_id:
            raise BrowserOperationNotFoundError(
                "browser operation was not found"
            )
        return snapshot

    def resolve_provider_scope(
        session_id: str,
        target: ProviderSessionTargetRequest,
    ) -> ProviderConnectionScope:
        if provider_connection_scope_resolver is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        try:
            scope = provider_connection_scope_resolver(session_id, target)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="provider connection was not found",
            ) from exc
        if (
            not isinstance(scope, ProviderConnectionScope)
            or scope.provider_connection_id != target.provider_connection_id
            or scope.provider != target.provider
            or scope.account_reference_hash != target.account_reference_hash
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="provider connection was not found",
            )
        return scope

    def provider_request_id(
        session_id: str,
        command: str,
        client_key: str,
        target: ProviderSessionTargetRequest,
    ) -> str:
        material = "\0".join(
            (
                session_id,
                command,
                client_key,
                target.provider,
                target.provider_connection_id,
                target.account_reference_hash or "unbound",
            )
        )
        digest = sha256(material.encode("utf-8")).hexdigest()
        return f"provider-session.{command}.{digest}"

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "jarvis-research-api"}

    @app.post(
        "/api/v1/sessions",
        response_model=CreateBrowserSessionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_session(request: Request) -> CreateBrowserSessionResponse:
        now = resolved_clock()
        session_id = resolved_session_ids()
        tenant_id = resolve_tenant_identity(request)
        access_token = resolved_authorizer.issue(session_id)
        ownership_bound = False
        try:
            if browser_session_ownership is not None and tenant_id is not None:
                bind_browser_ownership(session_id, tenant_id)
                ownership_bound = True
            if conversation is not None:
                conversation.open_session(session_id, at=now)
            session = operations.open_session(
                BrowserSessionSnapshot(
                    session_id=session_id,
                    created_at=now,
                    updated_at=now,
                )
            )
        except Exception:
            if conversation is not None:
                try:
                    conversation.close_session(session_id, at=now)
                except Exception:
                    pass
            if ownership_bound and browser_session_ownership is not None:
                try:
                    browser_session_ownership.unbind_browser_session(session_id)
                except Exception:
                    pass
            resolved_authorizer.revoke(session_id)
            raise
        return CreateBrowserSessionResponse(
            session=session,
            access_token=access_token,
        )

    @app.delete(
        "/api/v1/sessions/{session_id}",
        response_model=BrowserSessionSnapshot,
    )
    def close_session(
        session_id: str,
        x_jarvis_session_token: str | None = Header(default=None),
    ) -> BrowserSessionSnapshot:
        authorize(session_id, x_jarvis_session_token)
        closed = operations.close_session(
            session_id,
            closed_at=resolved_clock(),
        )
        try:
            if conversation is not None:
                conversation.close_session(session_id, at=resolved_clock())
        finally:
            try:
                unbind_browser_ownership(session_id)
            finally:
                resolved_authorizer.revoke(session_id)
        return closed

    if provider_sessions is not None:

        @app.post(
            "/api/v1/sessions/{session_id}/provider-session/status",
            response_model=ProviderSessionLifecycleResponse,
        )
        def provider_session_status(
            session_id: str,
            body: ProviderSessionStatusRequest,
            x_jarvis_session_token: str | None = Header(default=None),
        ) -> ProviderSessionLifecycleResponse:
            authorize(session_id, x_jarvis_session_token)
            scope = resolve_provider_scope(session_id, body.target)
            lifecycle = provider_sessions.status(
                request=ProviderSessionInspectionRequest(
                    request_id=provider_request_id(
                        session_id,
                        "status",
                        uuid4().hex,
                        body.target,
                    ),
                    connection=scope,
                    requested_at=resolved_clock(),
                )
            )
            return ProviderSessionLifecycleResponse.from_lifecycle(lifecycle)

        @app.post(
            "/api/v1/sessions/{session_id}/provider-session/provision",
            response_model=ProviderSessionLifecycleResponse,
        )
        def provision_provider_session(
            session_id: str,
            body: ProvisionProviderSessionRequest,
            x_jarvis_session_token: str | None = Header(default=None),
        ) -> ProviderSessionLifecycleResponse:
            authorize(session_id, x_jarvis_session_token)
            scope = resolve_provider_scope(session_id, body.target)
            lifecycle = provider_sessions.provision(
                request=ProviderSessionProvisioningRequest(
                    request_id=provider_request_id(
                        session_id,
                        "provision",
                        body.idempotency_key,
                        body.target,
                    ),
                    connection=scope,
                    requested_at=resolved_clock(),
                    user_interaction_authorized=(
                        body.user_interaction_authorized
                    ),
                    replace_existing=body.replace_existing,
                )
            )
            return ProviderSessionLifecycleResponse.from_lifecycle(lifecycle)

        @app.post(
            "/api/v1/sessions/{session_id}/provider-session/revoke",
            response_model=ProviderSessionLifecycleResponse,
        )
        def revoke_provider_session(
            session_id: str,
            body: RevokeProviderSessionRequest,
            x_jarvis_session_token: str | None = Header(default=None),
        ) -> ProviderSessionLifecycleResponse:
            authorize(session_id, x_jarvis_session_token)
            scope = resolve_provider_scope(session_id, body.target)
            lifecycle = provider_sessions.revoke(
                request=ProviderSessionRevocationRequest(
                    request_id=provider_request_id(
                        session_id,
                        "revoke",
                        body.idempotency_key,
                        body.target,
                    ),
                    connection=scope,
                    requested_at=resolved_clock(),
                    reason=body.reason,
                    secure_delete_required=body.secure_delete_required,
                )
            )
            return ProviderSessionLifecycleResponse.from_lifecycle(lifecycle)

    @app.post(
        "/api/v1/sessions/{session_id}/operations",
        response_model=BrowserOperationSnapshot,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def submit_operation(
        session_id: str,
        body: SubmitBrowserOperationRequest,
        x_jarvis_session_token: str | None = Header(default=None),
    ) -> BrowserOperationSnapshot:
        authorize(session_id, x_jarvis_session_token)
        if operations.get_session(session_id) is None:
            raise BrowserSessionNotFoundError("browser session was not found")
        return operations.submit(
            BrowserOperationRequest(
                operation_id=resolved_operation_ids(),
                session_id=session_id,
                idempotency_key=body.idempotency_key,
                kind=body.kind,
                input_channel=body.input_channel,
                message=body.message,
                requested_at=resolved_clock(),
            )
        )

    @app.get(
        "/api/v1/sessions/{session_id}/operations/{operation_id}",
        response_model=BrowserOperationSnapshot,
    )
    def get_operation(
        session_id: str,
        operation_id: str,
        x_jarvis_session_token: str | None = Header(default=None),
    ) -> BrowserOperationSnapshot:
        authorize(session_id, x_jarvis_session_token)
        return owned_operation(session_id, operation_id)

    @app.get(
        "/api/v1/sessions/{session_id}/operations/{operation_id}/result",
        response_model=BrowserOperationResultResponse,
    )
    def get_result(
        session_id: str,
        operation_id: str,
        x_jarvis_session_token: str | None = Header(default=None),
    ):
        authorize(session_id, x_jarvis_session_token)
        snapshot = owned_operation(session_id, operation_id)
        if snapshot.status is BrowserOperationStatus.COMPLETED:
            output = operations.get_result(operation_id)
            if output is None:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="completed browser operation result is unavailable",
                )
            return BrowserOperationResultResponse(
                operation=snapshot,
                output=output,
            )
        response = BrowserOperationResultResponse(operation=snapshot)
        if not snapshot.status.terminal:
            return JSONResponse(
                status_code=status.HTTP_202_ACCEPTED,
                content=response.model_dump(mode="json"),
            )
        if snapshot.status in {
            BrowserOperationStatus.FAILED,
            BrowserOperationStatus.CANCELLED,
        }:
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content=response.model_dump(mode="json"),
            )
        raise HTTPException(status_code=500, detail="invalid operation state")

    @app.get(
        "/api/v1/sessions/{session_id}/operations/{operation_id}"
        "/financial-documents/{cache_entry_id}",
        response_model=BrowserStructuredFinancialDocumentResponse,
    )
    def get_structured_financial_document(
        session_id: str,
        operation_id: str,
        cache_entry_id: str,
        x_jarvis_session_token: str | None = Header(default=None),
    ) -> BrowserStructuredFinancialDocumentResponse:
        authorize(session_id, x_jarvis_session_token)
        snapshot = owned_operation(session_id, operation_id)
        if snapshot.status is not BrowserOperationStatus.COMPLETED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="financial documents are available after completion",
            )
        output = operations.get_result(operation_id)
        if output is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="completed browser operation result is unavailable",
            )
        reference = next(
            (
                item
                for item in output.structured_document_references
                if item.cache_entry_id == cache_entry_id
            ),
            None,
        )
        if reference is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        if (
            structured_document_repository is None
            or structured_document_scope_resolver is None
        ):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        try:
            connection = structured_document_scope_resolver(session_id)
            if not isinstance(connection, ProviderConnectionScope):
                raise TypeError("invalid structured document scope")
            stored = (
                structured_document_repository
                .get_structured_financial_document_by_cache_entry_id(
                    cache_entry_id,
                    scope=StructuredDocumentRepositoryScope(
                        tenant_id=connection.tenant_id,
                        provider_connection_id=(
                            connection.provider_connection_id
                        ),
                        provider=connection.provider,
                    ),
                    as_of=resolved_clock(),
                )
            )
        except (StorageError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="financial document storage is currently unavailable",
            ) from exc
        if stored is None:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="financial document cache entry is no longer active",
            )
        document = stored.result.document
        if document is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="cached financial document is invalid",
            )
        try:
            return BrowserStructuredFinancialDocumentResponse(
                operation_id=operation_id,
                reference=reference,
                document=BrowserStructuredFinancialDocument.from_document(
                    document
                ),
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="cached financial document failed integrity checks",
            ) from exc

    @app.get(
        "/api/v1/sessions/{session_id}/operations/{operation_id}"
        "/benchmarking-financials/{cache_entry_id}",
        response_model=BrowserBenchmarkingFinancialsDocumentResponse,
    )
    def get_benchmarking_financials_document(
        session_id: str,
        operation_id: str,
        cache_entry_id: str,
        x_jarvis_session_token: str | None = Header(default=None),
    ) -> BrowserBenchmarkingFinancialsDocumentResponse:
        authorize(session_id, x_jarvis_session_token)
        snapshot = owned_operation(session_id, operation_id)
        if snapshot.status is not BrowserOperationStatus.COMPLETED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Benchmarking Financials are available after completion"
                ),
            )
        output = operations.get_result(operation_id)
        if output is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="completed browser operation result is unavailable",
            )
        reference = output.benchmarking_financials_reference
        if reference is None or reference.cache_entry_id != cache_entry_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        if (
            benchmarking_financials_repository is None
            or structured_document_scope_resolver is None
        ):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        try:
            connection = structured_document_scope_resolver(session_id)
            if not isinstance(connection, ProviderConnectionScope):
                raise TypeError("invalid Benchmarking Financials scope")
            stored = (
                benchmarking_financials_repository
                .get_benchmarking_financials_by_cache_entry_id(
                    cache_entry_id,
                    scope=StructuredDocumentRepositoryScope(
                        tenant_id=connection.tenant_id,
                        provider_connection_id=(
                            connection.provider_connection_id
                        ),
                        provider=connection.provider,
                    ),
                    as_of=resolved_clock(),
                )
            )
        except (StorageError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Benchmarking Financials storage is currently unavailable"
                ),
            ) from exc
        if stored is None:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail=(
                    "Benchmarking Financials cache entry is no longer active"
                ),
            )
        document = stored.result.document
        if document is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="cached Benchmarking Financials document is invalid",
            )
        try:
            return BrowserBenchmarkingFinancialsDocumentResponse(
                operation_id=operation_id,
                reference=reference,
                document=BrowserBenchmarkingFinancialsDocument.from_document(
                    document
                ),
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "cached Benchmarking Financials failed integrity checks"
                ),
            ) from exc

    @app.post(
        "/api/v1/sessions/{session_id}/operations/{operation_id}/cancel",
        response_model=BrowserOperationSnapshot,
    )
    def cancel_operation(
        session_id: str,
        operation_id: str,
        x_jarvis_session_token: str | None = Header(default=None),
    ) -> BrowserOperationSnapshot:
        authorize(session_id, x_jarvis_session_token)
        owned_operation(session_id, operation_id)
        return operations.cancel(operation_id)

    @app.get(
        "/api/v1/sessions/{session_id}/operations/{operation_id}/events",
        response_model=WorkflowEventBatch,
    )
    def replay_events(
        session_id: str,
        operation_id: str,
        after_sequence: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
        x_jarvis_session_token: str | None = Header(default=None),
    ) -> WorkflowEventBatch:
        authorize(session_id, x_jarvis_session_token)
        owned_operation(session_id, operation_id)
        return operations.replay(
            WorkflowEventReplayCursor(
                operation_id=operation_id,
                after_sequence=after_sequence,
                limit=limit,
            )
        )

    @app.get(
        "/api/v1/sessions/{session_id}/operations/{operation_id}/dashboard",
        response_model=JarvisDashboardView,
    )
    def get_dashboard(
        session_id: str,
        operation_id: str,
        x_jarvis_session_token: str | None = Header(default=None),
    ) -> JarvisDashboardView:
        authorize(session_id, x_jarvis_session_token)
        snapshot = owned_operation(session_id, operation_id)
        if snapshot.status is not BrowserOperationStatus.COMPLETED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="dashboard is available only after analysis completes",
            )
        output = operations.get_result(operation_id)
        if output is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="completed browser operation result is unavailable",
            )
        events = []
        sequence = 0
        while True:
            batch = operations.replay(
                WorkflowEventReplayCursor(
                    operation_id=operation_id,
                    after_sequence=sequence,
                    limit=500,
                )
            )
            events.extend(batch.events)
            sequence = batch.next_sequence
            if not batch.has_more:
                break
        try:
            return resolved_dashboard_projector.project(
                snapshot,
                output,
                events,
            )
        except DashboardProjectionError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

    if conversation is not None:

        @app.get(
            "/api/v1/sessions/{session_id}/conversation",
            response_model=BrowserConversationSnapshot,
        )
        def get_conversation(
            session_id: str,
            x_jarvis_session_token: str | None = Header(default=None),
        ) -> BrowserConversationSnapshot:
            authorize(session_id, x_jarvis_session_token)
            return conversation.get_snapshot(session_id, at=resolved_clock())

        @app.post(
            "/api/v1/sessions/{session_id}/conversation/turns",
            response_model=BrowserConversationTurn,
        )
        def submit_conversation_turn(
            session_id: str,
            body: BrowserConversationInputRequest,
            x_jarvis_session_token: str | None = Header(default=None),
        ) -> BrowserConversationTurn:
            authorize(session_id, x_jarvis_session_token)
            return conversation.handle(
                session_id,
                JarvisUtterance(
                    text=body.text,
                    channel=body.input_channel,
                ),
                idempotency_key=body.idempotency_key,
                operation_id_factory=resolved_operation_ids,
                at=resolved_clock(),
            )

        @app.post(
            "/api/v1/sessions/{session_id}/conversation/sleep",
            response_model=BrowserConversationSnapshot,
        )
        def sleep_conversation(
            session_id: str,
            x_jarvis_session_token: str | None = Header(default=None),
        ) -> BrowserConversationSnapshot:
            authorize(session_id, x_jarvis_session_token)
            return conversation.sleep(session_id, at=resolved_clock())

        @app.get(
            "/api/v1/sessions/{session_id}/conversation/events",
            response_model=ConversationEventBatch,
        )
        def replay_conversation_events(
            session_id: str,
            after_sequence: int = Query(default=0, ge=0),
            limit: int = Query(default=100, ge=1, le=500),
            x_jarvis_session_token: str | None = Header(default=None),
        ) -> ConversationEventBatch:
            authorize(session_id, x_jarvis_session_token)
            conversation.get_snapshot(session_id, at=resolved_clock())
            return conversation.replay(
                ConversationEventReplayCursor(
                    session_id=session_id,
                    after_sequence=after_sequence,
                    limit=limit,
                )
            )

        @app.get(
            "/api/v1/sessions/{session_id}/conversation/events/stream",
            response_class=StreamingResponse,
        )
        async def stream_conversation_events(
            request: Request,
            session_id: str,
            after_sequence: int | None = Query(default=None, ge=0),
            last_event_id: str | None = Header(
                default=None,
                alias="Last-Event-ID",
            ),
            x_jarvis_session_token: str | None = Header(default=None),
        ) -> StreamingResponse:
            authorize(session_id, x_jarvis_session_token)
            initial = conversation.get_snapshot(
                session_id,
                at=resolved_clock(),
            )
            cursor_sequence = _resolve_sse_cursor(
                session_id,
                after_sequence,
                last_event_id,
            )
            if cursor_sequence > initial.last_event_sequence:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="conversation cursor is beyond recorded progress",
                )

            async def conversation_stream():
                sequence = cursor_sequence
                loop = asyncio.get_running_loop()
                next_heartbeat = loop.time() + sse_heartbeat_seconds
                while True:
                    if await request.is_disconnected():
                        return
                    snapshot = conversation.get_snapshot(
                        session_id,
                        at=resolved_clock(),
                    )
                    batch = conversation.replay(
                        ConversationEventReplayCursor(
                            session_id=session_id,
                            after_sequence=sequence,
                            limit=500,
                        )
                    )
                    for event in batch.events:
                        sequence = event.sequence
                        yield _sse_frame(
                            event="conversation",
                            data=event.model_dump_json(),
                            event_id=event.event_id,
                        )
                        next_heartbeat = loop.time() + sse_heartbeat_seconds
                    if (
                        snapshot.state.value == "dormant"
                        and snapshot.last_event_sequence > 0
                    ):
                        yield _sse_frame(
                            event="conversation-terminal",
                            data=snapshot.model_dump_json(),
                        )
                        return
                    if batch.has_more:
                        continue
                    if not batch.events and loop.time() >= next_heartbeat:
                        yield ": jarvis-conversation-heartbeat\n\n"
                        next_heartbeat = loop.time() + sse_heartbeat_seconds
                    await asyncio.sleep(sse_poll_interval_seconds)

            return StreamingResponse(
                conversation_stream(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache, no-transform",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

    if speech is not None:

        @app.post("/api/v1/sessions/{session_id}/speech")
        def synthesize_speech(
            session_id: str,
            body: SpeechSynthesisRequest,
            x_jarvis_session_token: str | None = Header(default=None),
        ) -> Response:
            # Deliberate exception to "every route returns a pydantic
            # model": this is binary audio, not JSON-serializable data.
            authorize(session_id, x_jarvis_session_token)
            with prompt_audit_session_context(session_id):
                synthesis = speech.synthesize(text=body.text)
            return Response(
                content=synthesis.audio,
                media_type=synthesis.media_type,
            )

    if transcription is not None:

        @app.post(
            "/api/v1/sessions/{session_id}/transcribe",
            response_model=SpeechTranscriptionResponse,
        )
        async def transcribe_speech(
            session_id: str,
            request: Request,
            x_jarvis_session_token: str | None = Header(default=None),
        ) -> SpeechTranscriptionResponse:
            # Deliberate exception to "every request body is a validated
            # pydantic model": this is binary audio in, the mirror image
            # of /speech's binary audio out.
            authorize(session_id, x_jarvis_session_token)
            content_type = request.headers.get("content-type", "audio/webm")
            content_length = request.headers.get("content-length")
            if (
                content_length is not None
                and content_length.isdigit()
                and int(content_length) > _MAX_TRANSCRIPTION_AUDIO_BYTES
            ):
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail="uploaded audio exceeds the transcription size limit",
                )
            audio_bytes = await request.body()
            if not audio_bytes:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="transcription request body must not be empty",
                )
            if len(audio_bytes) > _MAX_TRANSCRIPTION_AUDIO_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail="uploaded audio exceeds the transcription size limit",
                )
            with prompt_audit_session_context(session_id):
                transcription_result = transcription.transcribe(
                    audio=audio_bytes,
                    media_type=content_type,
                )
            return SpeechTranscriptionResponse(
                transcript=transcription_result.transcript or None,
                confidence=transcription_result.confidence,
            )

    @app.get(
        "/api/v1/sessions/{session_id}/operations/{operation_id}/events/stream",
        response_class=StreamingResponse,
    )
    async def stream_events(
        request: Request,
        session_id: str,
        operation_id: str,
        after_sequence: int | None = Query(default=None, ge=0),
        last_event_id: str | None = Header(
            default=None,
            alias="Last-Event-ID",
        ),
        x_jarvis_session_token: str | None = Header(default=None),
    ) -> StreamingResponse:
        authorize(session_id, x_jarvis_session_token)
        initial_snapshot = owned_operation(session_id, operation_id)
        cursor_sequence = _resolve_sse_cursor(
            operation_id,
            after_sequence,
            last_event_id,
        )
        if cursor_sequence > initial_snapshot.last_event_sequence:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="event replay cursor is beyond recorded progress",
            )

        async def event_stream():
            sequence = cursor_sequence
            loop = asyncio.get_running_loop()
            next_heartbeat = loop.time() + sse_heartbeat_seconds
            while True:
                if await request.is_disconnected():
                    return
                batch = operations.replay(
                    WorkflowEventReplayCursor(
                        operation_id=operation_id,
                        after_sequence=sequence,
                        limit=500,
                    )
                )
                for event in batch.events:
                    sequence = event.sequence
                    yield _sse_frame(
                        event="workflow",
                        data=event.model_dump_json(),
                        event_id=event.event_id,
                    )
                    next_heartbeat = loop.time() + sse_heartbeat_seconds
                snapshot = owned_operation(session_id, operation_id)
                if snapshot.status.terminal:
                    yield _sse_frame(
                        event="terminal",
                        data=snapshot.model_dump_json(),
                    )
                    return
                if batch.has_more:
                    continue
                if not batch.events and loop.time() >= next_heartbeat:
                    yield ": jarvis-heartbeat\n\n"
                    next_heartbeat = loop.time() + sse_heartbeat_seconds
                await asyncio.sleep(sse_poll_interval_seconds)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return app


def _ist_now() -> datetime:
    return datetime.now(IST)


def _resolve_sse_cursor(
    operation_id: str,
    after_sequence: int | None,
    last_event_id: str | None,
) -> int:
    if after_sequence is not None:
        return after_sequence
    if last_event_id is None or not last_event_id.strip():
        return 0
    raw = last_event_id.strip()
    if ":" in raw:
        event_operation_id, separator, raw_sequence = raw.rpartition(":")
        if separator and event_operation_id != operation_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Last-Event-ID belongs to another operation",
            )
        raw = raw_sequence
    try:
        sequence = int(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Last-Event-ID must end with a non-negative sequence",
        ) from exc
    if sequence < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Last-Event-ID must end with a non-negative sequence",
        )
    return sequence


def _sse_frame(
    *,
    event: str,
    data: str,
    event_id: str | None = None,
) -> str:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.extend(f"data: {line}" for line in data.splitlines() or ("",))
    return "\n".join(lines) + "\n\n"
