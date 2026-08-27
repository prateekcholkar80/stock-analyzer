import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.models import (
    BrowserConversationInputRequest,
    BrowserOperationResultResponse,
    CreateBrowserSessionResponse,
    SpeechSynthesisRequest,
    SpeechTranscriptionResponse,
    SubmitBrowserOperationRequest,
)
from app.api.session_auth import (
    BrowserSessionAuthorizer,
    InMemoryBrowserSessionAuthorizer,
)
from app.audit.prompt_audit import prompt_audit_session_context
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
from app.presentation.dashboard import (
    DashboardProjectionError,
    JarvisDashboardProjector,
)
from app.stt.gateway import Transcription
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


def create_jarvis_http_app(
    operations: BrowserOperationApplication,
    *,
    conversation: BrowserConversationApplication | None = None,
    speech: SpeechSynthesisApplication | None = None,
    transcription: TranscriptionApplication | None = None,
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

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "jarvis-research-api"}

    @app.post(
        "/api/v1/sessions",
        response_model=CreateBrowserSessionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_session() -> CreateBrowserSessionResponse:
        now = resolved_clock()
        session_id = resolved_session_ids()
        access_token = resolved_authorizer.issue(session_id)
        try:
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
        if conversation is not None:
            conversation.close_session(session_id, at=resolved_clock())
        resolved_authorizer.revoke(session_id)
        return closed

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
