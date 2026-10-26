import json
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from re import fullmatch
from threading import RLock
from types import TracebackType

import duckdb
from pydantic import ValidationError

from app.exceptions import StorageConflictError, StorageError
from app.models.storage import (
    BacktestRunQuery,
    BacktestRunSummary,
    DebateRunQuery,
    DebateRunSummary,
    MarketSeriesQuery,
    MarketSeriesSummary,
    StoredBacktestRun,
    StoredDebateRun,
    StoredMarketSeries,
    backtest_run_summary,
    debate_run_summary,
    market_series_summary,
)
from app.models.fundamental_storage import (
    FundamentalRepositoryScope,
    FundamentalSnapshotCacheKey,
    FundamentalSnapshotQuery,
    FundamentalSnapshotSummary,
    StoredFundamentalSnapshot,
    fundamental_snapshot_summary,
)
from app.models.financial_document_storage import (
    StoredStructuredFinancialDocument,
    StructuredDocumentCacheKey,
    StructuredDocumentRepositoryScope,
)
from app.models.peer_comparison_storage import (
    PeerComparisonCacheKey,
    StoredPeerComparisonDocument,
)
from app.models.benchmarking_financials_storage import (
    BenchmarkingFinancialsCacheKey,
    StoredBenchmarkingFinancialsDocument,
)
from app.models.fundamental_package import FundamentalEvidencePackage
from app.models.fundamental_filings import (
    FundamentalFilingArtifactManifest,
    FundamentalFilingPackage,
)
from app.storage.fundamental_package_repositories import (
    FundamentalPackageRepositoryScope,
)
from app.storage.fundamental_filing_repositories import (
    FundamentalFilingDeleteAuthorization,
    FundamentalFilingDeleteReceipt,
    FundamentalFilingDeleteStatus,
    FundamentalFilingDeleteTarget,
    FundamentalFilingRepositoryScope,
)
from app.storage.fundamental_filing_evidence_repositories import (
    FundamentalFilingEvidencePolicyKey,
    StoredFundamentalFilingEvidence,
)
from app.storage.corporate_filings_analysis_repositories import (
    CorporateFilingsAnalysisDeleteAuthorization,
    CorporateFilingsAnalysisDeleteReceipt,
    CorporateFilingsAnalysisDeleteStatus,
    CorporateFilingsAnalysisPeriodKey,
    CorporateFilingsAnalysisRepositoryScope,
    StoredCorporateFilingsAnalysis,
)
from app.models.corporate_filings_analysis import (
    CorporateFilingsAnalysisReuseKey,
)


_SCHEMA_VERSION = 9
_STRUCTURED_CACHE_ENTRY_ID_PATTERN = r"^financial_document:[a-f0-9]{64}$"
_PEER_COMPARISON_CACHE_ENTRY_ID_PATTERN = r"^peer_comparison:[a-f0-9]{64}$"
_BENCHMARKING_FINANCIALS_CACHE_ENTRY_ID_PATTERN = (
    r"^benchmarking_financials:[a-f0-9]{64}$"
)
_FILING_FINGERPRINT_PATTERN = r"^[a-f0-9]{64}$"
_FILING_ARTIFACT_ID_PATTERN = r"^filing_artifact:[a-f0-9]{64}$"
_FILING_PACKAGE_ID_PATTERN = r"^filing_package:[a-f0-9]{64}$"
_FILING_EVIDENCE_RECORD_ID_PATTERN = (
    r"^filing_evidence_record:[a-f0-9]{64}$"
)
_CORPORATE_FILINGS_ANALYSIS_RECORD_ID_PATTERN = (
    r"^corporate_filings_analysis:[a-f0-9]{64}$"
)
_FILING_REFERENCE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,179}$"
_FILING_ACTIVE_REFERENCE_DETAIL = (
    "The filing artifact is referenced by a retained filing package."
)
_CORPORATE_FILINGS_ANALYSIS_ACTIVE_DETAIL = (
    "The corporate-filings analysis is the active successful result."
)
FundamentalCacheClock = Callable[[], datetime]


class DuckDBJarvisStorage:
    """Persistent DuckDB implementation of the Jarvis storage ports."""

    adapter_name = "duckdb"

    def __init__(
        self,
        database: str | Path,
        *,
        clock: FundamentalCacheClock | None = None,
    ) -> None:
        database_name = str(database)
        if not database_name.strip():
            raise ValueError("DuckDB database path cannot be blank")
        self._database = database_name
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = RLock()
        self._closed = False
        try:
            self._connection = duckdb.connect(database_name)
            self._initialize_schema()
            self.purge_expired_fundamental_snapshots(as_of=self._now())
            self.purge_expired_structured_financial_documents(
                as_of=self._now()
            )
            self.purge_expired_peer_comparisons(as_of=self._now())
            self.purge_expired_benchmarking_financials(as_of=self._now())
            self.purge_expired_fundamental_packages(as_of=self._now())
        except StorageError:
            self._close_after_initialization_failure()
            raise
        except (duckdb.Error, ValidationError, ValueError) as exc:
            self._close_after_initialization_failure()
            raise StorageError(
                "Unable to initialize DuckDB research storage"
            ) from exc

    @property
    def database(self) -> str:
        return self._database

    @property
    def schema_version(self) -> int:
        return _SCHEMA_VERSION

    def __enter__(self) -> "DuckDBJarvisStorage":
        self._ensure_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                self._connection.close()
            except duckdb.Error as exc:
                raise StorageError(
                    "Unable to close DuckDB research storage"
                ) from exc
            finally:
                self._closed = True

    def save_market_series(
        self,
        stored: StoredMarketSeries,
    ) -> StoredMarketSeries:
        value = StoredMarketSeries.model_validate(stored)
        summary = market_series_summary(value)
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                existing = self._connection.execute(
                    "SELECT payload_fingerprint, payload_json "
                    "FROM jarvis_market_series WHERE dataset_id = ?",
                    [value.dataset_id],
                ).fetchone()
                if existing is not None:
                    if existing[0] != value.payload_fingerprint:
                        raise StorageConflictError(
                            "market dataset identifier already contains "
                            "different data"
                        )
                    persisted = StoredMarketSeries.model_validate_json(
                        existing[1]
                    )
                else:
                    self._connection.execute(
                        "INSERT INTO jarvis_market_series ("
                        "dataset_id, payload_fingerprint, stored_at, "
                        "exchange, symbol_token, interval, source, "
                        "summary_json, payload_json"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [
                            value.dataset_id,
                            value.payload_fingerprint,
                            value.stored_at,
                            value.series.exchange,
                            value.series.symbol_token,
                            value.series.interval,
                            value.series.source,
                            summary.model_dump_json(),
                            value.model_dump_json(),
                        ],
                    )
                    persisted = value
                self._persist_market_candles(persisted)
                self._connection.execute("COMMIT")
                return StoredMarketSeries.model_validate_json(
                    persisted.model_dump_json()
                )
            except StorageConflictError:
                if transaction_started:
                    self._rollback_safely()
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to persist DuckDB market dataset"
                ) from exc

    def get_market_series(
        self,
        dataset_id: str,
    ) -> StoredMarketSeries | None:
        return self._get_payload(
            table="jarvis_market_series",
            id_column="dataset_id",
            identifier=dataset_id,
            payload_type=StoredMarketSeries,
        )

    def list_market_series(
        self,
        query: MarketSeriesQuery | None = None,
    ) -> tuple[MarketSeriesSummary, ...]:
        filters = query or MarketSeriesQuery()
        clauses, parameters = _common_filter_clauses(filters)
        _append_filter(
            clauses,
            parameters,
            "exchange",
            filters.exchange,
        )
        _append_filter(
            clauses,
            parameters,
            "symbol_token",
            filters.symbol_token,
        )
        _append_filter(
            clauses,
            parameters,
            "interval",
            filters.interval,
        )
        _append_filter(
            clauses,
            parameters,
            "source",
            filters.source,
        )
        return self._list_summaries(
            table="jarvis_market_series",
            id_column="dataset_id",
            clauses=clauses,
            parameters=parameters,
            limit=filters.limit,
            offset=filters.offset,
            summary_type=MarketSeriesSummary,
        )

    def delete_market_series(self, dataset_id: str) -> bool:
        return self._delete(
            table="jarvis_market_series",
            id_column="dataset_id",
            identifier=dataset_id,
            child_relations=(
                ("jarvis_market_candles", "dataset_id"),
                ("jarvis_instrument_symbols", "dataset_id"),
            ),
        )

    def save_backtest_run(
        self,
        stored: StoredBacktestRun,
    ) -> StoredBacktestRun:
        value = StoredBacktestRun.model_validate(stored)
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                existing = self._connection.execute(
                    "SELECT result_fingerprint, payload_json "
                    "FROM jarvis_backtest_runs WHERE run_id = ?",
                    [value.run_id],
                ).fetchone()
                if existing is not None:
                    if existing[0] != value.result_fingerprint:
                        raise StorageConflictError(
                            "backtest run identifier already contains "
                            "different data"
                        )
                    persisted = StoredBacktestRun.model_validate_json(
                        existing[1]
                    )
                else:
                    self._insert_backtest_parent(value)
                    persisted = value
                self._persist_backtest_details(persisted)
                self._connection.execute("COMMIT")
                return StoredBacktestRun.model_validate_json(
                    persisted.model_dump_json()
                )
            except StorageConflictError:
                if transaction_started:
                    self._rollback_safely()
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to persist normalized DuckDB backtest"
                ) from exc

    def get_backtest_run(
        self,
        run_id: str,
    ) -> StoredBacktestRun | None:
        return self._get_payload(
            table="jarvis_backtest_runs",
            id_column="run_id",
            identifier=run_id,
            payload_type=StoredBacktestRun,
        )

    def list_backtest_runs(
        self,
        query: BacktestRunQuery | None = None,
    ) -> tuple[BacktestRunSummary, ...]:
        filters = query or BacktestRunQuery()
        clauses, parameters = _common_filter_clauses(filters)
        _append_filter(
            clauses,
            parameters,
            "backtest_id",
            filters.backtest_id,
        )
        _append_filter(
            clauses,
            parameters,
            "engine_id",
            filters.engine_id,
        )
        _append_filter(
            clauses,
            parameters,
            "exchange",
            filters.exchange,
        )
        _append_filter(
            clauses,
            parameters,
            "symbol_token",
            filters.symbol_token,
        )
        _append_filter(
            clauses,
            parameters,
            "interval",
            filters.interval,
        )
        return self._list_summaries(
            table="jarvis_backtest_runs",
            id_column="run_id",
            clauses=clauses,
            parameters=parameters,
            limit=filters.limit,
            offset=filters.offset,
            summary_type=BacktestRunSummary,
        )

    def delete_backtest_run(self, run_id: str) -> bool:
        return self._delete(
            table="jarvis_backtest_runs",
            id_column="run_id",
            identifier=run_id,
            child_relations=(
                ("jarvis_backtest_evaluation_categories", "run_id"),
                ("jarvis_backtest_signal_evidence", "run_id"),
                ("jarvis_backtest_signal_contributions", "run_id"),
                ("jarvis_backtest_evaluations", "run_id"),
                ("jarvis_backtest_trades", "run_id"),
                ("jarvis_backtest_equity_points", "run_id"),
                ("jarvis_backtest_performance_segments", "run_id"),
                ("jarvis_backtest_performance", "run_id"),
            ),
        )

    def save_debate_run(
        self,
        stored: StoredDebateRun,
    ) -> StoredDebateRun:
        value = StoredDebateRun.model_validate(stored)
        summary = debate_run_summary(value)
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                existing = self._connection.execute(
                    "SELECT result_fingerprint, payload_json "
                    "FROM jarvis_debate_runs WHERE run_id = ?",
                    [value.run_id],
                ).fetchone()
                if existing is not None:
                    if existing[0] != value.result_fingerprint:
                        raise StorageConflictError(
                            "debate run identifier already contains "
                            "different data"
                        )
                    persisted = StoredDebateRun.model_validate_json(
                        existing[1]
                    )
                else:
                    self._connection.execute(
                        "INSERT INTO jarvis_debate_runs ("
                        "run_id, result_fingerprint, "
                        "technical_fingerprint, stored_at, exchange, "
                        "symbol_token, interval, winner, summary_json, "
                        "payload_json"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [
                            value.run_id,
                            value.result_fingerprint,
                            value.technical_fingerprint,
                            value.stored_at,
                            value.exchange,
                            value.symbol_token,
                            value.interval,
                            _enum_value(
                                value.result.submission.verdict.winner
                            ),
                            summary.model_dump_json(),
                            value.model_dump_json(),
                        ],
                    )
                    for token in value.signature:
                        self._connection.execute(
                            "INSERT INTO jarvis_debate_signal_signature "
                            "(run_id, token) VALUES (?, ?)",
                            [value.run_id, token],
                        )
                    persisted = value
                self._connection.execute("COMMIT")
                return StoredDebateRun.model_validate_json(
                    persisted.model_dump_json()
                )
            except StorageConflictError:
                if transaction_started:
                    self._rollback_safely()
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to persist DuckDB debate run"
                ) from exc

    def get_debate_run(
        self,
        run_id: str,
    ) -> StoredDebateRun | None:
        return self._get_payload(
            table="jarvis_debate_runs",
            id_column="run_id",
            identifier=run_id,
            payload_type=StoredDebateRun,
        )

    def list_debate_runs(
        self,
        query: DebateRunQuery | None = None,
    ) -> tuple[DebateRunSummary, ...]:
        filters = query or DebateRunQuery()
        clauses, parameters = _common_filter_clauses(filters)
        _append_filter(clauses, parameters, "exchange", filters.exchange)
        _append_filter(
            clauses,
            parameters,
            "symbol_token",
            filters.symbol_token,
        )
        _append_filter(clauses, parameters, "interval", filters.interval)
        _append_filter(
            clauses,
            parameters,
            "winner",
            _enum_value(filters.winner),
        )
        return self._list_summaries(
            table="jarvis_debate_runs",
            id_column="run_id",
            clauses=clauses,
            parameters=parameters,
            limit=filters.limit,
            offset=filters.offset,
            summary_type=DebateRunSummary,
        )

    def delete_debate_run(self, run_id: str) -> bool:
        return self._delete(
            table="jarvis_debate_runs",
            id_column="run_id",
            identifier=run_id,
            child_relations=(
                ("jarvis_debate_signal_signature", "run_id"),
            ),
        )

    def save_fundamental_snapshot(
        self,
        stored: StoredFundamentalSnapshot,
    ) -> StoredFundamentalSnapshot:
        value = StoredFundamentalSnapshot.model_validate(stored)
        summary = fundamental_snapshot_summary(value)
        now = self._now()
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                self._purge_expired_fundamental_locked(as_of=now)
                if value.stored_at > now:
                    raise StorageError(
                        "fundamental snapshot storage time is in the future"
                    )
                if value.is_expired(as_of=now):
                    raise StorageError(
                        "expired fundamental snapshot cannot be saved"
                    )

                existing = self._connection.execute(
                    "SELECT storage_fingerprint, payload_json "
                    "FROM jarvis_fundamental_snapshots "
                    "WHERE cache_entry_id = ?",
                    [value.cache_key.cache_entry_id],
                ).fetchone()
                if existing is not None:
                    if existing[0] != value.storage_fingerprint:
                        raise StorageConflictError(
                            "fundamental cache key already contains "
                            "different data"
                        )
                    persisted = StoredFundamentalSnapshot.model_validate_json(
                        existing[1]
                    )
                    if persisted.storage_fingerprint != existing[0]:
                        raise StorageError(
                            "DuckDB fundamental snapshot fingerprint is "
                            "inconsistent"
                        )
                else:
                    self._insert_fundamental_parent(value, summary)
                    persisted = value
                self._persist_fundamental_details(persisted)
                self._connection.execute("COMMIT")
                return StoredFundamentalSnapshot.model_validate_json(
                    persisted.model_dump_json(exclude_computed_fields=True)
                )
            except StorageError:
                if transaction_started:
                    self._rollback_safely()
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to persist DuckDB fundamental snapshot"
                ) from exc

    def replace_fundamental_snapshot(
        self,
        stored: StoredFundamentalSnapshot,
        *,
        scope: FundamentalRepositoryScope,
    ) -> StoredFundamentalSnapshot:
        """Transactionally install newer evidence for one scoped cache key."""

        value = StoredFundamentalSnapshot.model_validate(stored)
        caller_scope = FundamentalRepositoryScope.model_validate(scope)
        summary = fundamental_snapshot_summary(value)
        now = self._now()
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                self._purge_expired_fundamental_locked(as_of=now)
                if value.cache_key.repository_scope != caller_scope:
                    raise StorageError(
                        "fundamental replacement scope does not match cache key"
                    )
                if value.stored_at > now:
                    raise StorageError(
                        "fundamental snapshot storage time is in the future"
                    )
                if value.is_expired(as_of=now):
                    raise StorageError(
                        "expired fundamental snapshot cannot be saved"
                    )

                existing = self._connection.execute(
                    "SELECT storage_fingerprint, payload_json "
                    "FROM jarvis_fundamental_snapshots "
                    "WHERE cache_entry_id = ?",
                    [value.cache_key.cache_entry_id],
                ).fetchone()
                if existing is None:
                    persisted = value
                    self._insert_fundamental_parent(persisted, summary)
                else:
                    current = StoredFundamentalSnapshot.model_validate_json(
                        existing[1]
                    )
                    if current.storage_fingerprint != existing[0]:
                        raise StorageError(
                            "DuckDB fundamental snapshot fingerprint is "
                            "inconsistent"
                        )
                    if existing[0] == value.storage_fingerprint:
                        persisted = current
                    else:
                        if value.retrieved_at <= current.retrieved_at:
                            raise StorageConflictError(
                                "fundamental replacement is not newer than "
                                "cached data"
                            )
                        persisted = value
                        self._update_fundamental_parent(persisted, summary)

                self._persist_fundamental_details(persisted)
                self._connection.execute("COMMIT")
                return StoredFundamentalSnapshot.model_validate_json(
                    persisted.model_dump_json(exclude_computed_fields=True)
                )
            except StorageError:
                if transaction_started:
                    self._rollback_safely()
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to replace DuckDB fundamental snapshot"
                ) from exc

    def get_fundamental_snapshot(
        self,
        key: FundamentalSnapshotCacheKey,
        *,
        scope: FundamentalRepositoryScope,
        as_of: datetime,
    ) -> StoredFundamentalSnapshot | None:
        requested_key = FundamentalSnapshotCacheKey.model_validate(key)
        caller_scope = FundamentalRepositoryScope.model_validate(scope)
        read_at = _require_aware_datetime(
            as_of,
            "fundamental cache read time",
        )
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                self._purge_expired_fundamental_locked(as_of=read_at)
                if requested_key.repository_scope != caller_scope:
                    row = None
                else:
                    row = self._connection.execute(
                        "SELECT storage_fingerprint, payload_json "
                        "FROM jarvis_fundamental_snapshots "
                        "WHERE cache_entry_id = ? AND tenant_id = ? "
                        "AND provider_connection_id = ? AND provider = ? "
                        "AND stored_at <= ? AND expires_at > ?",
                        [
                            requested_key.cache_entry_id,
                            caller_scope.tenant_id,
                            caller_scope.provider_connection_id,
                            caller_scope.provider,
                            read_at,
                            read_at,
                        ],
                    ).fetchone()
                self._connection.execute("COMMIT")
                if row is None:
                    return None
                persisted = StoredFundamentalSnapshot.model_validate_json(
                    row[1]
                )
                if (
                    persisted.storage_fingerprint != row[0]
                    or persisted.cache_key != requested_key
                    or persisted.cache_key.repository_scope != caller_scope
                ):
                    raise StorageError(
                        "DuckDB fundamental snapshot failed integrity checks"
                    )
                return StoredFundamentalSnapshot.model_validate_json(
                    persisted.model_dump_json(exclude_computed_fields=True)
                )
            except StorageError:
                if transaction_started:
                    self._rollback_safely()
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to load DuckDB fundamental snapshot"
                ) from exc

    def list_fundamental_snapshots(
        self,
        query: FundamentalSnapshotQuery,
        *,
        as_of: datetime,
    ) -> tuple[FundamentalSnapshotSummary, ...]:
        filters = FundamentalSnapshotQuery.model_validate(query)
        read_at = _require_aware_datetime(
            as_of,
            "fundamental cache list time",
        )
        clauses = [
            "tenant_id = ?",
            "provider_connection_id = ?",
            "provider = ?",
            "stored_at <= ?",
            "expires_at > ?",
        ]
        parameters: list[object] = [
            filters.tenant_id,
            filters.provider_connection_id,
            filters.provider,
            read_at,
            read_at,
        ]
        _append_filter(clauses, parameters, "symbol", filters.symbol)
        _append_filter(clauses, parameters, "exchange", filters.exchange)
        if filters.capabilities:
            placeholders = ", ".join("?" for _ in filters.capabilities)
            clauses.append(f"capability IN ({placeholders})")
            parameters.extend(
                _enum_value(value)
                for value in filters.capabilities
            )
        if filters.retrieved_from is not None:
            clauses.append("retrieved_at >= ?")
            parameters.append(filters.retrieved_from)
        if filters.retrieved_to is not None:
            clauses.append("retrieved_at <= ?")
            parameters.append(filters.retrieved_to)
        sql = (
            "SELECT cache_entry_id, summary_json "
            "FROM jarvis_fundamental_snapshots WHERE "
            + " AND ".join(clauses)
            + " ORDER BY retrieved_at DESC, stored_at DESC, "
            "cache_entry_id DESC LIMIT ? OFFSET ?"
        )
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                self._purge_expired_fundamental_locked(as_of=read_at)
                rows = self._connection.execute(
                    sql,
                    [*parameters, filters.limit, filters.offset],
                ).fetchall()
                self._connection.execute("COMMIT")
                summaries = tuple(
                    FundamentalSnapshotSummary.model_validate_json(row[1])
                    for row in rows
                )
                if any(
                    summary.cache_entry_id != row[0]
                    or summary.tenant_id != filters.tenant_id
                    or summary.provider_connection_id
                    != filters.provider_connection_id
                    or summary.provider != filters.provider
                    for summary, row in zip(summaries, rows, strict=True)
                ):
                    raise StorageError(
                        "DuckDB fundamental summary failed integrity checks"
                    )
                return summaries
            except StorageError:
                if transaction_started:
                    self._rollback_safely()
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to list DuckDB fundamental snapshots"
                ) from exc

    def delete_fundamental_snapshot(
        self,
        key: FundamentalSnapshotCacheKey,
        *,
        scope: FundamentalRepositoryScope,
    ) -> bool:
        requested_key = FundamentalSnapshotCacheKey.model_validate(key)
        caller_scope = FundamentalRepositoryScope.model_validate(scope)
        now = self._now()
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                self._purge_expired_fundamental_locked(as_of=now)
                exists = None
                if requested_key.repository_scope == caller_scope:
                    exists = self._connection.execute(
                        "SELECT 1 FROM jarvis_fundamental_snapshots "
                        "WHERE cache_entry_id = ? AND tenant_id = ? "
                        "AND provider_connection_id = ? AND provider = ?",
                        [
                            requested_key.cache_entry_id,
                            caller_scope.tenant_id,
                            caller_scope.provider_connection_id,
                            caller_scope.provider,
                        ],
                    ).fetchone()
                if exists is not None:
                    self._delete_fundamental_entry_locked(
                        requested_key.cache_entry_id
                    )
                self._connection.execute("COMMIT")
                return exists is not None
            except duckdb.Error as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to delete DuckDB fundamental snapshot"
                ) from exc

    def purge_expired_fundamental_snapshots(
        self,
        *,
        as_of: datetime,
    ) -> int:
        purge_at = _require_aware_datetime(
            as_of,
            "fundamental cache purge time",
        )
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                removed = self._purge_expired_fundamental_locked(
                    as_of=purge_at
                )
                self._connection.execute("COMMIT")
                return removed
            except duckdb.Error as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to purge DuckDB fundamental snapshots"
                ) from exc

    def save_structured_financial_document(
        self,
        stored: StoredStructuredFinancialDocument,
    ) -> StoredStructuredFinancialDocument:
        value = StoredStructuredFinancialDocument.model_validate(stored)
        now = self._now()
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                self._purge_expired_structured_documents_locked(as_of=now)
                self._validate_structured_document_writable(value, as_of=now)
                existing = self._connection.execute(
                    "SELECT storage_fingerprint, payload_json "
                    "FROM jarvis_structured_financial_documents "
                    "WHERE cache_entry_id = ?",
                    [value.cache_key.cache_entry_id],
                ).fetchone()
                if existing is None:
                    self._insert_structured_document(value)
                    persisted = value
                else:
                    if existing[0] != value.storage_fingerprint:
                        raise StorageConflictError(
                            "structured document cache key contains different data"
                        )
                    persisted = StoredStructuredFinancialDocument.model_validate_json(
                        existing[1]
                    )
                    if persisted.storage_fingerprint != existing[0]:
                        raise StorageError(
                            "DuckDB structured document fingerprint is inconsistent"
                        )
                self._connection.execute("COMMIT")
                return self._copy_structured_document(persisted)
            except StorageError:
                if transaction_started:
                    self._rollback_safely()
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to persist DuckDB structured document"
                ) from exc

    def replace_structured_financial_document(
        self,
        stored: StoredStructuredFinancialDocument,
        *,
        scope: StructuredDocumentRepositoryScope,
    ) -> StoredStructuredFinancialDocument:
        value = StoredStructuredFinancialDocument.model_validate(stored)
        caller_scope = StructuredDocumentRepositoryScope.model_validate(scope)
        now = self._now()
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                self._purge_expired_structured_documents_locked(as_of=now)
                if value.cache_key.repository_scope != caller_scope:
                    raise StorageError(
                        "structured document replacement scope does not match key"
                    )
                self._validate_structured_document_writable(value, as_of=now)
                existing = self._connection.execute(
                    "SELECT storage_fingerprint, payload_json "
                    "FROM jarvis_structured_financial_documents "
                    "WHERE cache_entry_id = ?",
                    [value.cache_key.cache_entry_id],
                ).fetchone()
                if existing is None:
                    self._insert_structured_document(value)
                    persisted = value
                else:
                    current = StoredStructuredFinancialDocument.model_validate_json(
                        existing[1]
                    )
                    if current.storage_fingerprint != existing[0]:
                        raise StorageError(
                            "DuckDB structured document fingerprint is inconsistent"
                        )
                    if existing[0] == value.storage_fingerprint:
                        persisted = current
                    else:
                        if value.retrieved_at <= current.retrieved_at:
                            raise StorageConflictError(
                                "structured document replacement is not newer"
                            )
                        self._delete_structured_document_locked(
                            value.cache_key.cache_entry_id
                        )
                        self._insert_structured_document(value)
                        persisted = value
                self._connection.execute("COMMIT")
                return self._copy_structured_document(persisted)
            except StorageError:
                if transaction_started:
                    self._rollback_safely()
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to replace DuckDB structured document"
                ) from exc

    def replace_structured_financial_document_package(
        self,
        stored: tuple[StoredStructuredFinancialDocument, ...],
        *,
        scope: StructuredDocumentRepositoryScope,
    ) -> tuple[StoredStructuredFinancialDocument, ...]:
        """Replace all eleven financial documents in one transaction."""

        if not isinstance(stored, tuple) or len(stored) != 11:
            raise StorageError(
                "structured financial package must contain eleven documents"
            )
        values = tuple(
            StoredStructuredFinancialDocument.model_validate(item)
            for item in stored
        )
        caller_scope = StructuredDocumentRepositoryScope.model_validate(scope)
        now = self._now()
        cache_entry_ids = tuple(
            value.cache_key.cache_entry_id for value in values
        )
        if len(cache_entry_ids) != len(set(cache_entry_ids)):
            raise StorageConflictError(
                "structured financial package contains duplicate cache keys"
            )
        package_identities = {
            (
                value.cache_key.issuer.model_dump_json(),
                value.cache_key.as_of_date,
                value.retrieved_at,
                value.stored_at,
            )
            for value in values
        }
        if len(package_identities) != 1:
            raise StorageError(
                "structured financial package is not one immutable snapshot"
            )

        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                self._purge_expired_structured_documents_locked(as_of=now)
                actions = []
                for value in values:
                    if value.cache_key.repository_scope != caller_scope:
                        raise StorageError(
                            "structured financial package scope does not match key"
                        )
                    self._validate_structured_document_writable(value, as_of=now)
                    existing = self._connection.execute(
                        "SELECT storage_fingerprint, payload_json "
                        "FROM jarvis_structured_financial_documents "
                        "WHERE cache_entry_id = ?",
                        [value.cache_key.cache_entry_id],
                    ).fetchone()
                    if existing is None:
                        actions.append(("insert", value, value))
                        continue
                    current = (
                        StoredStructuredFinancialDocument.model_validate_json(
                            existing[1]
                        )
                    )
                    if current.storage_fingerprint != existing[0]:
                        raise StorageError(
                            "DuckDB structured document fingerprint is inconsistent"
                        )
                    if existing[0] == value.storage_fingerprint:
                        actions.append(("retain", value, current))
                        continue
                    if value.retrieved_at <= current.retrieved_at:
                        raise StorageConflictError(
                            "structured financial package is not newer than cache"
                        )
                    actions.append(("replace", value, value))

                for action, value, _persisted in actions:
                    if action == "retain":
                        continue
                    if action == "replace":
                        self._delete_structured_document_locked(
                            value.cache_key.cache_entry_id
                        )
                    self._insert_structured_document(value)
                self._connection.execute("COMMIT")
                return tuple(
                    self._copy_structured_document(persisted)
                    for _action, _value, persisted in actions
                )
            except StorageError:
                if transaction_started:
                    self._rollback_safely()
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to replace DuckDB structured financial package"
                ) from exc

    def get_structured_financial_document(
        self,
        key: StructuredDocumentCacheKey,
        *,
        scope: StructuredDocumentRepositoryScope,
        as_of: datetime,
    ) -> StoredStructuredFinancialDocument | None:
        requested_key = StructuredDocumentCacheKey.model_validate(key)
        caller_scope = StructuredDocumentRepositoryScope.model_validate(scope)
        read_at = _require_aware_datetime(
            as_of,
            "structured document cache read time",
        )
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                self._purge_expired_structured_documents_locked(as_of=read_at)
                row = None
                if requested_key.repository_scope == caller_scope:
                    row = self._connection.execute(
                        "SELECT storage_fingerprint, payload_json "
                        "FROM jarvis_structured_financial_documents "
                        "WHERE cache_entry_id = ? AND tenant_id = ? "
                        "AND provider_connection_id = ? AND provider = ? "
                        "AND stored_at <= ? AND expires_at > ?",
                        [
                            requested_key.cache_entry_id,
                            caller_scope.tenant_id,
                            caller_scope.provider_connection_id,
                            caller_scope.provider,
                            read_at,
                            read_at,
                        ],
                    ).fetchone()
                self._connection.execute("COMMIT")
                if row is None:
                    return None
                persisted = StoredStructuredFinancialDocument.model_validate_json(
                    row[1]
                )
                if (
                    persisted.storage_fingerprint != row[0]
                    or persisted.cache_key != requested_key
                    or persisted.cache_key.repository_scope != caller_scope
                ):
                    raise StorageError(
                        "DuckDB structured document failed integrity checks"
                    )
                return self._copy_structured_document(persisted)
            except StorageError:
                if transaction_started:
                    self._rollback_safely()
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to load DuckDB structured document"
                ) from exc

    def get_structured_financial_document_by_cache_entry_id(
        self,
        cache_entry_id: str,
        *,
        scope: StructuredDocumentRepositoryScope,
        as_of: datetime,
    ) -> StoredStructuredFinancialDocument | None:
        if (
            not isinstance(cache_entry_id, str)
            or fullmatch(
                _STRUCTURED_CACHE_ENTRY_ID_PATTERN,
                cache_entry_id,
            )
            is None
        ):
            raise ValueError("structured document cache entry ID is invalid")
        caller_scope = StructuredDocumentRepositoryScope.model_validate(scope)
        read_at = _require_aware_datetime(
            as_of,
            "structured document cache read time",
        )
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                self._purge_expired_structured_documents_locked(as_of=read_at)
                row = self._connection.execute(
                    "SELECT storage_fingerprint, payload_json "
                    "FROM jarvis_structured_financial_documents "
                    "WHERE cache_entry_id = ? AND tenant_id = ? "
                    "AND provider_connection_id = ? AND provider = ? "
                    "AND stored_at <= ? AND expires_at > ?",
                    [
                        cache_entry_id,
                        caller_scope.tenant_id,
                        caller_scope.provider_connection_id,
                        caller_scope.provider,
                        read_at,
                        read_at,
                    ],
                ).fetchone()
                self._connection.execute("COMMIT")
                if row is None:
                    return None
                persisted = StoredStructuredFinancialDocument.model_validate_json(
                    row[1]
                )
                if (
                    persisted.storage_fingerprint != row[0]
                    or persisted.cache_key.cache_entry_id != cache_entry_id
                    or persisted.cache_key.repository_scope != caller_scope
                ):
                    raise StorageError(
                        "DuckDB structured document failed integrity checks"
                    )
                return self._copy_structured_document(persisted)
            except StorageError:
                if transaction_started:
                    self._rollback_safely()
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to resolve DuckDB structured document reference"
                ) from exc

    def delete_structured_financial_document(
        self,
        key: StructuredDocumentCacheKey,
        *,
        scope: StructuredDocumentRepositoryScope,
    ) -> bool:
        requested_key = StructuredDocumentCacheKey.model_validate(key)
        caller_scope = StructuredDocumentRepositoryScope.model_validate(scope)
        now = self._now()
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                self._purge_expired_structured_documents_locked(as_of=now)
                exists = None
                if requested_key.repository_scope == caller_scope:
                    exists = self._connection.execute(
                        "SELECT 1 FROM jarvis_structured_financial_documents "
                        "WHERE cache_entry_id = ? AND tenant_id = ? "
                        "AND provider_connection_id = ? AND provider = ?",
                        [
                            requested_key.cache_entry_id,
                            caller_scope.tenant_id,
                            caller_scope.provider_connection_id,
                            caller_scope.provider,
                        ],
                    ).fetchone()
                if exists is not None:
                    self._delete_structured_document_locked(
                        requested_key.cache_entry_id
                    )
                self._connection.execute("COMMIT")
                return exists is not None
            except duckdb.Error as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to delete DuckDB structured document"
                ) from exc

    def purge_expired_structured_financial_documents(
        self,
        *,
        as_of: datetime,
    ) -> int:
        purge_at = _require_aware_datetime(
            as_of,
            "structured document purge time",
        )
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                removed = self._purge_expired_structured_documents_locked(
                    as_of=purge_at
                )
                self._connection.execute("COMMIT")
                return removed
            except duckdb.Error as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to purge DuckDB structured documents"
                ) from exc

    def save_peer_comparison(
        self,
        stored: StoredPeerComparisonDocument,
    ) -> StoredPeerComparisonDocument:
        value = StoredPeerComparisonDocument.model_validate(stored)
        now = self._now()
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                self._purge_expired_peer_comparisons_locked(as_of=now)
                self._validate_peer_comparison_writable(value, as_of=now)
                existing = self._connection.execute(
                    "SELECT storage_fingerprint, payload_json "
                    "FROM jarvis_peer_comparisons WHERE cache_entry_id = ?",
                    [value.cache_key.cache_entry_id],
                ).fetchone()
                if existing is None:
                    self._insert_peer_comparison(value)
                    persisted = value
                else:
                    if existing[0] != value.storage_fingerprint:
                        raise StorageConflictError(
                            "Peer Comparison cache key contains different data"
                        )
                    persisted = StoredPeerComparisonDocument.model_validate_json(
                        existing[1]
                    )
                    if persisted.storage_fingerprint != existing[0]:
                        raise StorageError(
                            "DuckDB Peer Comparison fingerprint is inconsistent"
                        )
                self._connection.execute("COMMIT")
                return self._copy_peer_comparison(persisted)
            except StorageError:
                if transaction_started:
                    self._rollback_safely()
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to persist DuckDB Peer Comparison"
                ) from exc

    def replace_peer_comparison(
        self,
        stored: StoredPeerComparisonDocument,
        *,
        scope: StructuredDocumentRepositoryScope,
    ) -> StoredPeerComparisonDocument:
        value = StoredPeerComparisonDocument.model_validate(stored)
        caller_scope = StructuredDocumentRepositoryScope.model_validate(scope)
        now = self._now()
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                self._purge_expired_peer_comparisons_locked(as_of=now)
                if value.cache_key.repository_scope != caller_scope:
                    raise StorageError(
                        "Peer Comparison replacement scope does not match key"
                    )
                self._validate_peer_comparison_writable(value, as_of=now)
                existing = self._connection.execute(
                    "SELECT storage_fingerprint, payload_json "
                    "FROM jarvis_peer_comparisons WHERE cache_entry_id = ?",
                    [value.cache_key.cache_entry_id],
                ).fetchone()
                if existing is None:
                    self._insert_peer_comparison(value)
                    persisted = value
                else:
                    current = StoredPeerComparisonDocument.model_validate_json(
                        existing[1]
                    )
                    if current.storage_fingerprint != existing[0]:
                        raise StorageError(
                            "DuckDB Peer Comparison fingerprint is inconsistent"
                        )
                    if existing[0] == value.storage_fingerprint:
                        persisted = current
                    else:
                        if value.retrieved_at <= current.retrieved_at:
                            raise StorageConflictError(
                                "Peer Comparison replacement is not newer than cache"
                            )
                        self._delete_peer_comparison_locked(
                            value.cache_key.cache_entry_id
                        )
                        self._insert_peer_comparison(value)
                        persisted = value
                self._connection.execute("COMMIT")
                return self._copy_peer_comparison(persisted)
            except StorageError:
                if transaction_started:
                    self._rollback_safely()
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to replace DuckDB Peer Comparison"
                ) from exc

    def get_peer_comparison(
        self,
        key: PeerComparisonCacheKey,
        *,
        scope: StructuredDocumentRepositoryScope,
        as_of: datetime,
    ) -> StoredPeerComparisonDocument | None:
        requested_key = PeerComparisonCacheKey.model_validate(key)
        caller_scope = StructuredDocumentRepositoryScope.model_validate(scope)
        read_at = _require_aware_datetime(as_of, "Peer Comparison cache read time")
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                self._purge_expired_peer_comparisons_locked(as_of=read_at)
                row = None
                if requested_key.repository_scope == caller_scope:
                    row = self._connection.execute(
                        "SELECT storage_fingerprint, payload_json "
                        "FROM jarvis_peer_comparisons "
                        "WHERE cache_entry_id = ? AND tenant_id = ? "
                        "AND provider_connection_id = ? AND provider = ? "
                        "AND stored_at <= ? AND expires_at > ?",
                        [
                            requested_key.cache_entry_id,
                            caller_scope.tenant_id,
                            caller_scope.provider_connection_id,
                            caller_scope.provider,
                            read_at,
                            read_at,
                        ],
                    ).fetchone()
                self._connection.execute("COMMIT")
                if row is None:
                    return None
                persisted = StoredPeerComparisonDocument.model_validate_json(row[1])
                if (
                    persisted.storage_fingerprint != row[0]
                    or persisted.cache_key != requested_key
                    or persisted.cache_key.repository_scope != caller_scope
                ):
                    raise StorageError(
                        "DuckDB Peer Comparison failed integrity checks"
                    )
                return self._copy_peer_comparison(persisted)
            except StorageError:
                if transaction_started:
                    self._rollback_safely()
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to load DuckDB Peer Comparison"
                ) from exc

    def get_peer_comparison_by_cache_entry_id(
        self,
        cache_entry_id: str,
        *,
        scope: StructuredDocumentRepositoryScope,
        as_of: datetime,
    ) -> StoredPeerComparisonDocument | None:
        if (
            not isinstance(cache_entry_id, str)
            or fullmatch(_PEER_COMPARISON_CACHE_ENTRY_ID_PATTERN, cache_entry_id)
            is None
        ):
            raise ValueError("Peer Comparison cache entry ID is invalid")
        caller_scope = StructuredDocumentRepositoryScope.model_validate(scope)
        read_at = _require_aware_datetime(as_of, "Peer Comparison cache read time")
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                self._purge_expired_peer_comparisons_locked(as_of=read_at)
                row = self._connection.execute(
                    "SELECT storage_fingerprint, payload_json "
                    "FROM jarvis_peer_comparisons "
                    "WHERE cache_entry_id = ? AND tenant_id = ? "
                    "AND provider_connection_id = ? AND provider = ? "
                    "AND stored_at <= ? AND expires_at > ?",
                    [
                        cache_entry_id,
                        caller_scope.tenant_id,
                        caller_scope.provider_connection_id,
                        caller_scope.provider,
                        read_at,
                        read_at,
                    ],
                ).fetchone()
                self._connection.execute("COMMIT")
                if row is None:
                    return None
                persisted = StoredPeerComparisonDocument.model_validate_json(row[1])
                if (
                    persisted.storage_fingerprint != row[0]
                    or persisted.cache_key.cache_entry_id != cache_entry_id
                    or persisted.cache_key.repository_scope != caller_scope
                ):
                    raise StorageError(
                        "DuckDB Peer Comparison failed integrity checks"
                    )
                return self._copy_peer_comparison(persisted)
            except StorageError:
                if transaction_started:
                    self._rollback_safely()
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to resolve DuckDB Peer Comparison reference"
                ) from exc

    def delete_peer_comparison(
        self,
        key: PeerComparisonCacheKey,
        *,
        scope: StructuredDocumentRepositoryScope,
    ) -> bool:
        requested_key = PeerComparisonCacheKey.model_validate(key)
        caller_scope = StructuredDocumentRepositoryScope.model_validate(scope)
        now = self._now()
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                self._purge_expired_peer_comparisons_locked(as_of=now)
                exists = None
                if requested_key.repository_scope == caller_scope:
                    exists = self._connection.execute(
                        "SELECT 1 FROM jarvis_peer_comparisons "
                        "WHERE cache_entry_id = ? AND tenant_id = ? "
                        "AND provider_connection_id = ? AND provider = ?",
                        [
                            requested_key.cache_entry_id,
                            caller_scope.tenant_id,
                            caller_scope.provider_connection_id,
                            caller_scope.provider,
                        ],
                    ).fetchone()
                if exists is not None:
                    self._delete_peer_comparison_locked(
                        requested_key.cache_entry_id
                    )
                self._connection.execute("COMMIT")
                return exists is not None
            except duckdb.Error as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to delete DuckDB Peer Comparison"
                ) from exc

    def purge_expired_peer_comparisons(self, *, as_of: datetime) -> int:
        purge_at = _require_aware_datetime(as_of, "Peer Comparison purge time")
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                removed = self._purge_expired_peer_comparisons_locked(
                    as_of=purge_at
                )
                self._connection.execute("COMMIT")
                return removed
            except duckdb.Error as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to purge DuckDB Peer Comparisons"
                ) from exc

    def save_benchmarking_financials(
        self,
        stored: StoredBenchmarkingFinancialsDocument,
    ) -> StoredBenchmarkingFinancialsDocument:
        value = StoredBenchmarkingFinancialsDocument.model_validate(stored)
        now = self._now()
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                self._purge_expired_benchmarking_financials_locked(as_of=now)
                self._validate_benchmarking_financials_writable(
                    value,
                    as_of=now,
                )
                existing = self._connection.execute(
                    "SELECT storage_fingerprint, payload_json "
                    "FROM jarvis_benchmarking_financials "
                    "WHERE cache_entry_id = ?",
                    [value.cache_key.cache_entry_id],
                ).fetchone()
                if existing is None:
                    self._insert_benchmarking_financials(value)
                    persisted = value
                else:
                    if existing[0] != value.storage_fingerprint:
                        raise StorageConflictError(
                            "Benchmarking Financials cache key contains different data"
                        )
                    persisted = (
                        StoredBenchmarkingFinancialsDocument.model_validate_json(
                            existing[1]
                        )
                    )
                    if persisted.storage_fingerprint != existing[0]:
                        raise StorageError(
                            "DuckDB Benchmarking Financials fingerprint is inconsistent"
                        )
                self._connection.execute("COMMIT")
                return self._copy_benchmarking_financials(persisted)
            except StorageError:
                if transaction_started:
                    self._rollback_safely()
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to persist DuckDB Benchmarking Financials"
                ) from exc

    def replace_benchmarking_financials(
        self,
        stored: StoredBenchmarkingFinancialsDocument,
        *,
        scope: StructuredDocumentRepositoryScope,
    ) -> StoredBenchmarkingFinancialsDocument:
        value = StoredBenchmarkingFinancialsDocument.model_validate(stored)
        caller_scope = StructuredDocumentRepositoryScope.model_validate(scope)
        now = self._now()
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                self._purge_expired_benchmarking_financials_locked(as_of=now)
                if value.cache_key.repository_scope != caller_scope:
                    raise StorageError(
                        "Benchmarking Financials replacement scope does not match key"
                    )
                self._validate_benchmarking_financials_writable(
                    value,
                    as_of=now,
                )
                existing = self._connection.execute(
                    "SELECT storage_fingerprint, payload_json "
                    "FROM jarvis_benchmarking_financials "
                    "WHERE cache_entry_id = ?",
                    [value.cache_key.cache_entry_id],
                ).fetchone()
                if existing is None:
                    self._insert_benchmarking_financials(value)
                    persisted = value
                else:
                    current = (
                        StoredBenchmarkingFinancialsDocument.model_validate_json(
                            existing[1]
                        )
                    )
                    if current.storage_fingerprint != existing[0]:
                        raise StorageError(
                            "DuckDB Benchmarking Financials fingerprint is inconsistent"
                        )
                    if existing[0] == value.storage_fingerprint:
                        persisted = current
                    else:
                        if value.retrieved_at <= current.retrieved_at:
                            raise StorageConflictError(
                                "Benchmarking Financials replacement is not newer than cache"
                            )
                        self._delete_benchmarking_financials_locked(
                            value.cache_key.cache_entry_id
                        )
                        self._insert_benchmarking_financials(value)
                        persisted = value
                self._connection.execute("COMMIT")
                return self._copy_benchmarking_financials(persisted)
            except StorageError:
                if transaction_started:
                    self._rollback_safely()
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to replace DuckDB Benchmarking Financials"
                ) from exc

    def get_benchmarking_financials(
        self,
        key: BenchmarkingFinancialsCacheKey,
        *,
        scope: StructuredDocumentRepositoryScope,
        as_of: datetime,
    ) -> StoredBenchmarkingFinancialsDocument | None:
        requested_key = BenchmarkingFinancialsCacheKey.model_validate(key)
        caller_scope = StructuredDocumentRepositoryScope.model_validate(scope)
        read_at = _require_aware_datetime(
            as_of,
            "Benchmarking Financials cache read time",
        )
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                self._purge_expired_benchmarking_financials_locked(
                    as_of=read_at
                )
                row = None
                if requested_key.repository_scope == caller_scope:
                    row = self._connection.execute(
                        "SELECT storage_fingerprint, payload_json "
                        "FROM jarvis_benchmarking_financials "
                        "WHERE cache_entry_id = ? AND tenant_id = ? "
                        "AND provider_connection_id = ? AND provider = ? "
                        "AND stored_at <= ? AND expires_at > ?",
                        [
                            requested_key.cache_entry_id,
                            caller_scope.tenant_id,
                            caller_scope.provider_connection_id,
                            caller_scope.provider,
                            read_at,
                            read_at,
                        ],
                    ).fetchone()
                self._connection.execute("COMMIT")
                if row is None:
                    return None
                persisted = (
                    StoredBenchmarkingFinancialsDocument.model_validate_json(
                        row[1]
                    )
                )
                if (
                    persisted.storage_fingerprint != row[0]
                    or persisted.cache_key != requested_key
                    or persisted.cache_key.repository_scope != caller_scope
                ):
                    raise StorageError(
                        "DuckDB Benchmarking Financials failed integrity checks"
                    )
                return self._copy_benchmarking_financials(persisted)
            except StorageError:
                if transaction_started:
                    self._rollback_safely()
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to load DuckDB Benchmarking Financials"
                ) from exc

    def get_benchmarking_financials_by_cache_entry_id(
        self,
        cache_entry_id: str,
        *,
        scope: StructuredDocumentRepositoryScope,
        as_of: datetime,
    ) -> StoredBenchmarkingFinancialsDocument | None:
        if (
            not isinstance(cache_entry_id, str)
            or fullmatch(
                _BENCHMARKING_FINANCIALS_CACHE_ENTRY_ID_PATTERN,
                cache_entry_id,
            )
            is None
        ):
            raise ValueError(
                "Benchmarking Financials cache entry ID is invalid"
            )
        caller_scope = StructuredDocumentRepositoryScope.model_validate(scope)
        read_at = _require_aware_datetime(
            as_of,
            "Benchmarking Financials cache read time",
        )
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                self._purge_expired_benchmarking_financials_locked(
                    as_of=read_at
                )
                row = self._connection.execute(
                    "SELECT storage_fingerprint, payload_json "
                    "FROM jarvis_benchmarking_financials "
                    "WHERE cache_entry_id = ? AND tenant_id = ? "
                    "AND provider_connection_id = ? AND provider = ? "
                    "AND stored_at <= ? AND expires_at > ?",
                    [
                        cache_entry_id,
                        caller_scope.tenant_id,
                        caller_scope.provider_connection_id,
                        caller_scope.provider,
                        read_at,
                        read_at,
                    ],
                ).fetchone()
                self._connection.execute("COMMIT")
                if row is None:
                    return None
                persisted = (
                    StoredBenchmarkingFinancialsDocument.model_validate_json(
                        row[1]
                    )
                )
                if (
                    persisted.storage_fingerprint != row[0]
                    or persisted.cache_key.cache_entry_id != cache_entry_id
                    or persisted.cache_key.repository_scope != caller_scope
                ):
                    raise StorageError(
                        "DuckDB Benchmarking Financials failed integrity checks"
                    )
                return self._copy_benchmarking_financials(persisted)
            except StorageError:
                if transaction_started:
                    self._rollback_safely()
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to resolve DuckDB Benchmarking Financials reference"
                ) from exc

    def delete_benchmarking_financials(
        self,
        key: BenchmarkingFinancialsCacheKey,
        *,
        scope: StructuredDocumentRepositoryScope,
    ) -> bool:
        requested_key = BenchmarkingFinancialsCacheKey.model_validate(key)
        caller_scope = StructuredDocumentRepositoryScope.model_validate(scope)
        now = self._now()
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                self._purge_expired_benchmarking_financials_locked(as_of=now)
                exists = None
                if requested_key.repository_scope == caller_scope:
                    exists = self._connection.execute(
                        "SELECT 1 FROM jarvis_benchmarking_financials "
                        "WHERE cache_entry_id = ? AND tenant_id = ? "
                        "AND provider_connection_id = ? AND provider = ?",
                        [
                            requested_key.cache_entry_id,
                            caller_scope.tenant_id,
                            caller_scope.provider_connection_id,
                            caller_scope.provider,
                        ],
                    ).fetchone()
                if exists is not None:
                    self._delete_benchmarking_financials_locked(
                        requested_key.cache_entry_id
                    )
                self._connection.execute("COMMIT")
                return exists is not None
            except duckdb.Error as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to delete DuckDB Benchmarking Financials"
                ) from exc

    def purge_expired_benchmarking_financials(
        self,
        *,
        as_of: datetime,
    ) -> int:
        purge_at = _require_aware_datetime(
            as_of,
            "Benchmarking Financials purge time",
        )
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                removed = self._purge_expired_benchmarking_financials_locked(
                    as_of=purge_at
                )
                self._connection.execute("COMMIT")
                return removed
            except duckdb.Error as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to purge DuckDB Benchmarking Financials"
                ) from exc

    def commit_fundamental_package(
        self,
        package: FundamentalEvidencePackage,
        *,
        expected_active_package_id: str | None,
    ) -> FundamentalEvidencePackage:
        value = FundamentalEvidencePackage.model_validate(package)
        now = self._now()
        scope = FundamentalPackageRepositoryScope.from_package(value)
        scope_fingerprint = self._fundamental_package_scope_fingerprint(scope)
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                self._purge_expired_fundamental_packages_locked(as_of=now)
                self._validate_fundamental_package_writable(value, as_of=now)
                row = self._connection.execute(
                    "SELECT package_id FROM jarvis_active_fundamental_packages "
                    "WHERE scope_fingerprint = ?",
                    [scope_fingerprint],
                ).fetchone()
                active_package_id = row[0] if row is not None else None
                if active_package_id != expected_active_package_id:
                    raise StorageConflictError(
                        "active fundamental package changed before commit"
                    )

                existing = self._connection.execute(
                    "SELECT aggregate_fingerprint "
                    "FROM jarvis_fundamental_packages WHERE package_id = ?",
                    [value.package_id],
                ).fetchone()
                if (
                    existing is not None
                    and existing[0] != value.aggregate_fingerprint
                ):
                    raise StorageConflictError(
                        "fundamental package ID contains different data"
                    )
                if existing is None:
                    self._insert_fundamental_package(value, scope_fingerprint)

                self._connection.execute(
                    "DELETE FROM jarvis_active_fundamental_packages "
                    "WHERE scope_fingerprint = ?",
                    [scope_fingerprint],
                )
                self._connection.execute(
                    "INSERT INTO jarvis_active_fundamental_packages "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        scope_fingerprint,
                        value.package_id,
                        scope.connection.tenant_id,
                        scope.connection.provider_connection_id,
                        scope.connection.provider,
                        scope.issuer.exchange,
                        scope.issuer.symbol,
                        now,
                    ],
                )
                self._connection.execute("COMMIT")
                return self._copy_fundamental_package(value)
            except (StorageConflictError, StorageError):
                if transaction_started:
                    self._rollback_safely()
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to commit DuckDB fundamental package"
                ) from exc

    def get_active_fundamental_package(
        self,
        *,
        scope: FundamentalPackageRepositoryScope,
        as_of: datetime,
    ) -> FundamentalEvidencePackage | None:
        caller_scope = FundamentalPackageRepositoryScope.model_validate(scope)
        read_at = _require_aware_datetime(
            as_of,
            "fundamental package read time",
        )
        scope_fingerprint = self._fundamental_package_scope_fingerprint(
            caller_scope
        )
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                self._purge_expired_fundamental_packages_locked(as_of=read_at)
                row = self._connection.execute(
                    "SELECT p.payload_json "
                    "FROM jarvis_active_fundamental_packages AS a "
                    "JOIN jarvis_fundamental_packages AS p "
                    "ON p.package_id = a.package_id "
                    "WHERE a.scope_fingerprint = ? AND p.committed_at <= ?",
                    [scope_fingerprint, read_at],
                ).fetchone()
                self._connection.execute("COMMIT")
                if row is None:
                    return None
                package = FundamentalEvidencePackage.model_validate_json(row[0])
                if FundamentalPackageRepositoryScope.from_package(
                    package
                ) != caller_scope:
                    return None
                return self._copy_fundamental_package(package)
            except (duckdb.Error, ValidationError, ValueError) as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to read active DuckDB fundamental package"
                ) from exc

    def get_fundamental_package(
        self,
        package_id: str,
        *,
        scope: FundamentalPackageRepositoryScope,
        as_of: datetime,
    ) -> FundamentalEvidencePackage | None:
        if not isinstance(package_id, str) or fullmatch(
            r"fundamental_package:[a-f0-9]{64}", package_id
        ) is None:
            raise ValueError("fundamental package ID is invalid")
        caller_scope = FundamentalPackageRepositoryScope.model_validate(scope)
        read_at = _require_aware_datetime(
            as_of,
            "fundamental package read time",
        )
        scope_fingerprint = self._fundamental_package_scope_fingerprint(
            caller_scope
        )
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                self._purge_expired_fundamental_packages_locked(as_of=read_at)
                row = self._connection.execute(
                    "SELECT payload_json FROM jarvis_fundamental_packages "
                    "WHERE package_id = ? AND scope_fingerprint = ? "
                    "AND committed_at <= ?",
                    [package_id, scope_fingerprint, read_at],
                ).fetchone()
                self._connection.execute("COMMIT")
                if row is None:
                    return None
                return self._copy_fundamental_package(
                    FundamentalEvidencePackage.model_validate_json(row[0])
                )
            except (duckdb.Error, ValidationError, ValueError) as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to read DuckDB fundamental package"
                ) from exc

    def purge_expired_fundamental_packages(
        self,
        *,
        as_of: datetime,
    ) -> int:
        purge_at = _require_aware_datetime(
            as_of,
            "fundamental package purge time",
        )
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                removed = self._purge_expired_fundamental_packages_locked(
                    as_of=purge_at
                )
                self._connection.execute("COMMIT")
                return removed
            except duckdb.Error as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to purge DuckDB fundamental packages"
                ) from exc

    def save_filing_artifact(
        self,
        artifact: FundamentalFilingArtifactManifest,
    ) -> FundamentalFilingArtifactManifest:
        value = FundamentalFilingArtifactManifest.model_validate(artifact)
        now = self._now()
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                self._validate_filing_artifact_writable(value, as_of=now)
                self._validate_filing_artifact_links_locked(value, pending={})
                persisted = self._save_filing_artifact_locked(value)
                self._connection.execute("COMMIT")
                return persisted
            except (StorageConflictError, StorageError):
                if transaction_started:
                    self._rollback_safely()
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to persist DuckDB filing artifact metadata"
                ) from exc

    def save_filing_evidence(
        self,
        record: StoredFundamentalFilingEvidence,
    ) -> StoredFundamentalFilingEvidence:
        value = StoredFundamentalFilingEvidence.model_validate(record)
        now = self._now()
        if value.stored_at > now:
            raise StorageError(
                "fundamental filing evidence storage time is in the future"
            )
        scope = value.scope
        scope_fingerprint = self._filing_scope_fingerprint(scope)
        reference = value.evidence.artifact.reference
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                row = self._connection.execute(
                    "SELECT payload_json "
                    "FROM jarvis_fundamental_filing_evidence "
                    "WHERE record_id = ?",
                    [value.record_id],
                ).fetchone()
                if row is not None:
                    existing = self._decode_scoped_filing_evidence(
                        row[0],
                        scope=scope,
                    )
                    if existing != value:
                        raise StorageConflictError(
                            "filing evidence record ID contains different data"
                        )
                    self._connection.execute("COMMIT")
                    return self._copy_filing_evidence(existing)

                self._connection.execute(
                    "INSERT INTO jarvis_fundamental_filing_evidence VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        value.record_id,
                        value.record_fingerprint,
                        scope_fingerprint,
                        reference.connection.tenant_id,
                        reference.connection.provider_connection_id,
                        reference.connection.provider,
                        reference.issuer.exchange,
                        reference.issuer.symbol,
                        reference.issuer.isin,
                        value.artifact_id,
                        value.artifact_fingerprint,
                        value.extraction_policy_fingerprint,
                        value.validation_policy_fingerprint,
                        value.extraction_fingerprint,
                        value.validation_fingerprint,
                        value.releaseable,
                        value.evidence.extracted_at,
                        value.stored_at,
                        value.to_storage_json(),
                    ],
                )
                self._connection.execute("COMMIT")
                return self._copy_filing_evidence(value)
            except (StorageConflictError, StorageError):
                if transaction_started:
                    self._rollback_safely()
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to persist DuckDB filing evidence"
                ) from exc

    def get_filing_evidence(
        self,
        record_id: str,
        *,
        scope: FundamentalFilingRepositoryScope,
        as_of: datetime,
    ) -> StoredFundamentalFilingEvidence | None:
        self._validate_filing_identifier(
            record_id,
            pattern=_FILING_EVIDENCE_RECORD_ID_PATTERN,
            label="filing evidence record ID",
        )
        caller_scope = FundamentalFilingRepositoryScope.model_validate(scope)
        read_at = _require_aware_datetime(as_of, "filing evidence read time")
        scope_fingerprint = self._filing_scope_fingerprint(caller_scope)
        with self._lock:
            self._ensure_open()
            try:
                row = self._connection.execute(
                    "SELECT payload_json "
                    "FROM jarvis_fundamental_filing_evidence "
                    "WHERE record_id = ? AND scope_fingerprint = ? "
                    "AND stored_at <= ?",
                    [record_id, scope_fingerprint, read_at],
                ).fetchone()
                if row is None:
                    return None
                value = self._decode_scoped_filing_evidence(
                    row[0],
                    scope=caller_scope,
                )
                if value.record_id != record_id:
                    raise StorageError(
                        "DuckDB filing evidence record identity is inconsistent"
                    )
                return self._copy_filing_evidence(value)
            except StorageError:
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                raise StorageError(
                    "Unable to read DuckDB filing evidence"
                ) from exc

    def list_filing_evidence_for_artifact(
        self,
        artifact_id: str,
        *,
        scope: FundamentalFilingRepositoryScope,
        as_of: datetime,
    ) -> tuple[StoredFundamentalFilingEvidence, ...]:
        self._validate_filing_identifier(
            artifact_id,
            pattern=_FILING_ARTIFACT_ID_PATTERN,
            label="filing artifact ID",
        )
        caller_scope = FundamentalFilingRepositoryScope.model_validate(scope)
        read_at = _require_aware_datetime(as_of, "filing evidence read time")
        scope_fingerprint = self._filing_scope_fingerprint(caller_scope)
        with self._lock:
            self._ensure_open()
            try:
                rows = self._connection.execute(
                    "SELECT payload_json "
                    "FROM jarvis_fundamental_filing_evidence "
                    "WHERE scope_fingerprint = ? AND artifact_id = ? "
                    "AND stored_at <= ? "
                    "ORDER BY extracted_at, stored_at, record_id",
                    [scope_fingerprint, artifact_id, read_at],
                ).fetchall()
                values = tuple(
                    self._decode_scoped_filing_evidence(
                        row[0],
                        scope=caller_scope,
                    )
                    for row in rows
                )
                if any(value.artifact_id != artifact_id for value in values):
                    raise StorageError(
                        "DuckDB filing evidence artifact identity is inconsistent"
                    )
                return tuple(
                    self._copy_filing_evidence(value) for value in values
                )
            except StorageError:
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                raise StorageError(
                    "Unable to list DuckDB filing evidence"
                ) from exc

    def find_filing_evidence_by_policy(
        self,
        key: FundamentalFilingEvidencePolicyKey,
        *,
        scope: FundamentalFilingRepositoryScope,
        as_of: datetime,
    ) -> tuple[StoredFundamentalFilingEvidence, ...]:
        policy_key = FundamentalFilingEvidencePolicyKey.model_validate(key)
        caller_scope = FundamentalFilingRepositoryScope.model_validate(scope)
        read_at = _require_aware_datetime(as_of, "filing evidence read time")
        scope_fingerprint = self._filing_scope_fingerprint(caller_scope)
        with self._lock:
            self._ensure_open()
            try:
                rows = self._connection.execute(
                    "SELECT payload_json "
                    "FROM jarvis_fundamental_filing_evidence "
                    "WHERE scope_fingerprint = ? AND artifact_id = ? "
                    "AND artifact_fingerprint = ? "
                    "AND extraction_policy_fingerprint = ? "
                    "AND validation_policy_fingerprint = ? "
                    "AND stored_at <= ? "
                    "ORDER BY extracted_at, stored_at, record_id",
                    [
                        scope_fingerprint,
                        policy_key.artifact_id,
                        policy_key.artifact_fingerprint,
                        policy_key.extraction_policy_fingerprint,
                        policy_key.validation_policy_fingerprint,
                        read_at,
                    ],
                ).fetchall()
                values = tuple(
                    self._decode_scoped_filing_evidence(
                        row[0],
                        scope=caller_scope,
                    )
                    for row in rows
                )
                if any(
                    FundamentalFilingEvidencePolicyKey.from_record(value)
                    != policy_key
                    for value in values
                ):
                    raise StorageError(
                        "DuckDB filing evidence policy identity is inconsistent"
                    )
                return tuple(
                    self._copy_filing_evidence(value) for value in values
                )
            except StorageError:
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                raise StorageError(
                    "Unable to query DuckDB filing evidence by policy"
                ) from exc

    def save_corporate_filings_analysis(
        self,
        record: StoredCorporateFilingsAnalysis,
    ) -> StoredCorporateFilingsAnalysis:
        value = self._copy_corporate_filings_analysis(
            StoredCorporateFilingsAnalysis.model_validate(record)
        )
        now = self._now()
        self._validate_corporate_filings_analysis_writable(value, as_of=now)
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                existing = self._validate_corporate_analysis_conflicts_locked(
                    value
                )
                if existing is None:
                    self._insert_corporate_filings_analysis_locked(value)
                    existing = value
                self._connection.execute("COMMIT")
                return self._copy_corporate_filings_analysis(existing)
            except (StorageConflictError, StorageError):
                if transaction_started:
                    self._rollback_safely()
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to persist DuckDB corporate-filings analysis"
                ) from exc

    def find_corporate_filings_analysis_by_reuse_key(
        self,
        reuse_key: CorporateFilingsAnalysisReuseKey,
        *,
        scope: CorporateFilingsAnalysisRepositoryScope,
        as_of: datetime,
    ) -> StoredCorporateFilingsAnalysis | None:
        key = CorporateFilingsAnalysisReuseKey.model_validate(reuse_key)
        caller_scope = CorporateFilingsAnalysisRepositoryScope.model_validate(
            scope
        )
        read_at = _require_aware_datetime(
            as_of,
            "corporate analysis reuse read time",
        )
        with self._lock:
            self._ensure_open()
            try:
                rows = self._connection.execute(
                    "SELECT payload_json "
                    "FROM jarvis_corporate_filings_analysis "
                    "WHERE scope_fingerprint = ? "
                    "AND reuse_key_fingerprint = ? AND stored_at <= ?",
                    [
                        caller_scope.scope_fingerprint,
                        key.reuse_key_fingerprint,
                        read_at,
                    ],
                ).fetchall()
                if not rows:
                    return None
                if len(rows) != 1:
                    raise StorageError(
                        "DuckDB corporate analysis reuse index is ambiguous"
                    )
                value = self._decode_scoped_corporate_filings_analysis(
                    rows[0][0],
                    scope=caller_scope,
                )
                if value.reuse_key != key:
                    raise StorageError(
                        "DuckDB corporate analysis reuse metadata is inconsistent"
                    )
                return self._copy_corporate_filings_analysis(value)
            except StorageError:
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                raise StorageError(
                    "Unable to query DuckDB corporate analysis by reuse key"
                ) from exc

    def list_corporate_filings_analysis_history(
        self,
        period_key: CorporateFilingsAnalysisPeriodKey,
        *,
        scope: CorporateFilingsAnalysisRepositoryScope,
        as_of: datetime,
    ) -> tuple[StoredCorporateFilingsAnalysis, ...]:
        key = CorporateFilingsAnalysisPeriodKey.model_validate(period_key)
        caller_scope = CorporateFilingsAnalysisRepositoryScope.model_validate(
            scope
        )
        read_at = _require_aware_datetime(
            as_of,
            "corporate analysis history read time",
        )
        with self._lock:
            self._ensure_open()
            try:
                rows = self._connection.execute(
                    "SELECT payload_json "
                    "FROM jarvis_corporate_filings_analysis "
                    "WHERE scope_fingerprint = ? "
                    "AND period_key_fingerprint = ? AND stored_at <= ? "
                    "ORDER BY stored_at, analysis_completed_at, record_id",
                    [
                        caller_scope.scope_fingerprint,
                        key.period_key_fingerprint,
                        read_at,
                    ],
                ).fetchall()
                values = tuple(
                    self._decode_scoped_corporate_filings_analysis(
                        row[0],
                        scope=caller_scope,
                    )
                    for row in rows
                )
                if any(value.period_key != key for value in values):
                    raise StorageError(
                        "DuckDB corporate analysis period metadata is inconsistent"
                    )
                return tuple(
                    self._copy_corporate_filings_analysis(value)
                    for value in values
                )
            except StorageError:
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                raise StorageError(
                    "Unable to list DuckDB corporate analysis history"
                ) from exc

    def get_active_corporate_filings_analysis(
        self,
        period_key: CorporateFilingsAnalysisPeriodKey,
        *,
        scope: CorporateFilingsAnalysisRepositoryScope,
        as_of: datetime,
    ) -> StoredCorporateFilingsAnalysis | None:
        key = CorporateFilingsAnalysisPeriodKey.model_validate(period_key)
        caller_scope = CorporateFilingsAnalysisRepositoryScope.model_validate(
            scope
        )
        read_at = _require_aware_datetime(
            as_of,
            "active corporate analysis read time",
        )
        with self._lock:
            self._ensure_open()
            try:
                row = self._connection.execute(
                    "SELECT r.payload_json "
                    "FROM jarvis_corporate_filings_analysis_active_history AS h "
                    "JOIN jarvis_corporate_filings_analysis AS r "
                    "ON r.record_id = h.record_id "
                    "WHERE h.scope_fingerprint = ? "
                    "AND h.period_key_fingerprint = ? "
                    "AND h.activated_at <= ? AND r.stored_at <= ? "
                    "ORDER BY h.activated_at DESC, "
                    "h.activation_sequence DESC LIMIT 1",
                    [
                        caller_scope.scope_fingerprint,
                        key.period_key_fingerprint,
                        read_at,
                        read_at,
                    ],
                ).fetchone()
                if row is None:
                    return None
                value = self._decode_scoped_corporate_filings_analysis(
                    row[0],
                    scope=caller_scope,
                )
                if value.period_key != key:
                    raise StorageError(
                        "DuckDB active corporate analysis is inconsistent"
                    )
                return self._copy_corporate_filings_analysis(value)
            except StorageError:
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                raise StorageError(
                    "Unable to read active DuckDB corporate analysis"
                ) from exc

    def commit_corporate_filings_analysis(
        self,
        record: StoredCorporateFilingsAnalysis,
        *,
        expected_active_record_id: str | None,
    ) -> StoredCorporateFilingsAnalysis:
        value = self._copy_corporate_filings_analysis(
            StoredCorporateFilingsAnalysis.model_validate(record)
        )
        self._validate_optional_corporate_analysis_record_id(
            expected_active_record_id
        )
        now = self._now()
        self._validate_corporate_filings_analysis_writable(value, as_of=now)
        scope_fingerprint = value.scope.scope_fingerprint
        period_key_fingerprint = value.period_key.period_key_fingerprint
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                row = self._connection.execute(
                    "SELECT record_id "
                    "FROM jarvis_active_corporate_filings_analysis "
                    "WHERE scope_fingerprint = ? "
                    "AND period_key_fingerprint = ?",
                    [scope_fingerprint, period_key_fingerprint],
                ).fetchone()
                active_record_id = row[0] if row is not None else None
                if active_record_id != expected_active_record_id:
                    raise StorageConflictError(
                        "active corporate filings analysis changed before commit"
                    )

                existing = self._validate_corporate_analysis_conflicts_locked(
                    value
                )
                if active_record_id == value.record_id:
                    if existing is None:
                        raise StorageError(
                            "active corporate analysis record is unavailable"
                        )
                    self._connection.execute("COMMIT")
                    return self._copy_corporate_filings_analysis(existing)

                if existing is None:
                    self._insert_corporate_filings_analysis_locked(value)
                    existing = value
                sequence_row = self._connection.execute(
                    "SELECT COALESCE(MAX(activation_sequence), 0) + 1 "
                    "FROM jarvis_corporate_filings_analysis_active_history "
                    "WHERE scope_fingerprint = ? "
                    "AND period_key_fingerprint = ?",
                    [scope_fingerprint, period_key_fingerprint],
                ).fetchone()
                activation_sequence = int(sequence_row[0])
                self._connection.execute(
                    "DELETE FROM jarvis_active_corporate_filings_analysis "
                    "WHERE scope_fingerprint = ? "
                    "AND period_key_fingerprint = ?",
                    [scope_fingerprint, period_key_fingerprint],
                )
                self._connection.execute(
                    "INSERT INTO jarvis_active_corporate_filings_analysis "
                    "VALUES (?, ?, ?, ?)",
                    [
                        scope_fingerprint,
                        period_key_fingerprint,
                        value.record_id,
                        now,
                    ],
                )
                self._connection.execute(
                    "INSERT INTO "
                    "jarvis_corporate_filings_analysis_active_history "
                    "VALUES (?, ?, ?, ?, ?)",
                    [
                        scope_fingerprint,
                        period_key_fingerprint,
                        activation_sequence,
                        value.record_id,
                        now,
                    ],
                )
                self._connection.execute("COMMIT")
                return self._copy_corporate_filings_analysis(existing)
            except (StorageConflictError, StorageError):
                if transaction_started:
                    self._rollback_safely()
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to commit DuckDB corporate-filings analysis"
                ) from exc

    def delete_corporate_filings_analysis(
        self,
        record_id: str,
        *,
        scope: CorporateFilingsAnalysisRepositoryScope,
        authorization: CorporateFilingsAnalysisDeleteAuthorization,
    ) -> CorporateFilingsAnalysisDeleteReceipt:
        self._validate_corporate_analysis_record_id(record_id)
        caller_scope = CorporateFilingsAnalysisRepositoryScope.model_validate(
            scope
        )
        approved = CorporateFilingsAnalysisDeleteAuthorization.model_validate(
            authorization
        )
        if approved.record_id != record_id:
            raise StorageError(
                "corporate analysis deletion authorization mismatches target"
            )
        completed_at = self._now()
        if approved.authorized_at > completed_at:
            raise StorageError(
                "corporate analysis deletion authorization is in the future"
            )
        scope_fingerprint = caller_scope.scope_fingerprint
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                prior = self._read_corporate_analysis_delete_receipt_locked(
                    approved
                )
                if prior is not None:
                    self._connection.execute("COMMIT")
                    return prior

                row = self._connection.execute(
                    "SELECT payload_json "
                    "FROM jarvis_corporate_filings_analysis "
                    "WHERE record_id = ? AND scope_fingerprint = ?",
                    [record_id, scope_fingerprint],
                ).fetchone()
                detail = None
                if row is None:
                    status = CorporateFilingsAnalysisDeleteStatus.NOT_FOUND
                else:
                    stored = self._decode_scoped_corporate_filings_analysis(
                        row[0],
                        scope=caller_scope,
                    )
                    if approved.record_fingerprint != stored.record_fingerprint:
                        raise StorageError(
                            "corporate analysis deletion fingerprint "
                            "mismatches target"
                        )
                    active = self._connection.execute(
                        "SELECT 1 "
                        "FROM jarvis_active_corporate_filings_analysis "
                        "WHERE scope_fingerprint = ? AND record_id = ? "
                        "LIMIT 1",
                        [scope_fingerprint, record_id],
                    ).fetchone()
                    if active is not None:
                        status = (
                            CorporateFilingsAnalysisDeleteStatus
                            .REJECTED_ACTIVE_RESULT
                        )
                        detail = _CORPORATE_FILINGS_ANALYSIS_ACTIVE_DETAIL
                    else:
                        self._connection.execute(
                            "DELETE FROM "
                            "jarvis_corporate_filings_analysis_active_history "
                            "WHERE scope_fingerprint = ? AND record_id = ?",
                            [scope_fingerprint, record_id],
                        )
                        self._connection.execute(
                            "DELETE FROM jarvis_corporate_filings_analysis "
                            "WHERE record_id = ? AND scope_fingerprint = ?",
                            [record_id, scope_fingerprint],
                        )
                        status = CorporateFilingsAnalysisDeleteStatus.DELETED
                receipt = CorporateFilingsAnalysisDeleteReceipt(
                    authorization=approved,
                    status=status,
                    completed_at=completed_at,
                    sanitized_detail=detail,
                )
                self._insert_corporate_analysis_delete_receipt_locked(receipt)
                self._connection.execute("COMMIT")
                return self._copy_corporate_analysis_delete_receipt(receipt)
            except (StorageConflictError, StorageError):
                if transaction_started:
                    self._rollback_safely()
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to delete DuckDB corporate-filings analysis"
                ) from exc

    def find_filing_artifacts_by_content_hash(
        self,
        content_sha256: str,
        *,
        scope: FundamentalFilingRepositoryScope,
    ) -> tuple[FundamentalFilingArtifactManifest, ...]:
        self._validate_filing_identifier(
            content_sha256,
            pattern=_FILING_FINGERPRINT_PATTERN,
            label="filing content SHA-256",
        )
        caller_scope = FundamentalFilingRepositoryScope.model_validate(scope)
        scope_fingerprint = self._filing_scope_fingerprint(caller_scope)
        with self._lock:
            self._ensure_open()
            try:
                rows = self._connection.execute(
                    "SELECT payload_json "
                    "FROM jarvis_fundamental_filing_artifacts "
                    "WHERE scope_fingerprint = ? AND content_sha256 = ? "
                    "ORDER BY downloaded_at, last_checked_at, artifact_id",
                    [scope_fingerprint, content_sha256],
                ).fetchall()
                return tuple(
                    self._copy_filing_artifact(
                        FundamentalFilingArtifactManifest.model_validate_json(
                            row[0]
                        )
                    )
                    for row in rows
                )
            except (duckdb.Error, ValidationError, ValueError) as exc:
                raise StorageError(
                    "Unable to query DuckDB filing artifacts by hash"
                ) from exc

    def get_filing_artifact(
        self,
        artifact_id: str,
        *,
        scope: FundamentalFilingRepositoryScope,
    ) -> FundamentalFilingArtifactManifest | None:
        self._validate_filing_identifier(
            artifact_id,
            pattern=_FILING_ARTIFACT_ID_PATTERN,
            label="filing artifact ID",
        )
        caller_scope = FundamentalFilingRepositoryScope.model_validate(scope)
        scope_fingerprint = self._filing_scope_fingerprint(caller_scope)
        with self._lock:
            self._ensure_open()
            try:
                row = self._connection.execute(
                    "SELECT payload_json "
                    "FROM jarvis_fundamental_filing_artifacts "
                    "WHERE artifact_id = ? AND scope_fingerprint = ?",
                    [artifact_id, scope_fingerprint],
                ).fetchone()
                if row is None:
                    return None
                return self._copy_filing_artifact(
                    FundamentalFilingArtifactManifest.model_validate_json(
                        row[0]
                    )
                )
            except (duckdb.Error, ValidationError, ValueError) as exc:
                raise StorageError(
                    "Unable to read DuckDB filing artifact metadata"
                ) from exc

    def list_filing_artifact_versions(
        self,
        filing_reference_id: str,
        *,
        scope: FundamentalFilingRepositoryScope,
    ) -> tuple[FundamentalFilingArtifactManifest, ...]:
        self._validate_filing_identifier(
            filing_reference_id,
            pattern=_FILING_REFERENCE_ID_PATTERN,
            label="filing reference ID",
        )
        caller_scope = FundamentalFilingRepositoryScope.model_validate(scope)
        scope_fingerprint = self._filing_scope_fingerprint(caller_scope)
        with self._lock:
            self._ensure_open()
            try:
                rows = self._connection.execute(
                    "SELECT payload_json "
                    "FROM jarvis_fundamental_filing_artifacts "
                    "WHERE scope_fingerprint = ? AND filing_reference_id = ? "
                    "ORDER BY downloaded_at, last_checked_at, artifact_id",
                    [scope_fingerprint, filing_reference_id],
                ).fetchall()
                return tuple(
                    self._copy_filing_artifact(
                        FundamentalFilingArtifactManifest.model_validate_json(
                            row[0]
                        )
                    )
                    for row in rows
                )
            except (duckdb.Error, ValidationError, ValueError) as exc:
                raise StorageError(
                    "Unable to list DuckDB filing artifact versions"
                ) from exc

    def commit_filing_package(
        self,
        package: FundamentalFilingPackage,
        *,
        expected_active_package_id: str | None,
    ) -> FundamentalFilingPackage:
        value = FundamentalFilingPackage.model_validate(package)
        self._validate_optional_filing_package_id(expected_active_package_id)
        now = self._now()
        scope = FundamentalFilingRepositoryScope.from_package(value)
        scope_fingerprint = self._filing_scope_fingerprint(scope)
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                self._validate_filing_package_writable(value, as_of=now)
                active_row = self._connection.execute(
                    "SELECT package_id "
                    "FROM jarvis_active_fundamental_filing_packages "
                    "WHERE scope_fingerprint = ?",
                    [scope_fingerprint],
                ).fetchone()
                active_package_id = (
                    active_row[0] if active_row is not None else None
                )
                if active_package_id != expected_active_package_id:
                    raise StorageConflictError(
                        "active filing package changed before commit"
                    )

                existing_row = self._connection.execute(
                    "SELECT payload_json "
                    "FROM jarvis_fundamental_filing_packages "
                    "WHERE package_id = ?",
                    [value.package_id],
                ).fetchone()
                if existing_row is not None:
                    existing = FundamentalFilingPackage.model_validate_json(
                        existing_row[0]
                    )
                    if existing != value:
                        raise StorageConflictError(
                            "filing package ID contains different data"
                        )

                pending = {
                    artifact.artifact_id: artifact
                    for artifact in value.artifacts
                }
                for artifact in value.artifacts:
                    self._validate_filing_artifact_writable(
                        artifact,
                        as_of=now,
                    )
                    self._validate_filing_artifact_links_locked(
                        artifact,
                        pending=pending,
                    )
                    self._validate_filing_artifact_conflict_locked(artifact)

                for artifact in value.artifacts:
                    self._save_filing_artifact_locked(artifact)
                if existing_row is None:
                    self._insert_filing_package_locked(
                        value,
                        scope_fingerprint=scope_fingerprint,
                    )

                manifest = value.manifest
                self._connection.execute(
                    "INSERT INTO jarvis_active_fundamental_filing_packages "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (scope_fingerprint) DO UPDATE SET "
                    "package_id = excluded.package_id, "
                    "tenant_id = excluded.tenant_id, "
                    "provider_connection_id = excluded.provider_connection_id, "
                    "provider = excluded.provider, "
                    "exchange = excluded.exchange, "
                    "symbol = excluded.symbol, "
                    "updated_at = excluded.updated_at",
                    [
                        scope_fingerprint,
                        value.package_id,
                        manifest.connection.tenant_id,
                        manifest.connection.provider_connection_id,
                        manifest.connection.provider,
                        manifest.issuer.exchange,
                        manifest.issuer.symbol,
                        now,
                    ],
                )
                self._connection.execute("COMMIT")
                return self._copy_filing_package(value)
            except (StorageConflictError, StorageError):
                if transaction_started:
                    self._rollback_safely()
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to commit DuckDB filing package"
                ) from exc

    def get_active_filing_package(
        self,
        *,
        scope: FundamentalFilingRepositoryScope,
    ) -> FundamentalFilingPackage | None:
        caller_scope = FundamentalFilingRepositoryScope.model_validate(scope)
        scope_fingerprint = self._filing_scope_fingerprint(caller_scope)
        with self._lock:
            self._ensure_open()
            try:
                row = self._connection.execute(
                    "SELECT p.payload_json "
                    "FROM jarvis_active_fundamental_filing_packages AS a "
                    "JOIN jarvis_fundamental_filing_packages AS p "
                    "ON p.package_id = a.package_id "
                    "WHERE a.scope_fingerprint = ?",
                    [scope_fingerprint],
                ).fetchone()
                if row is None:
                    return None
                return self._copy_filing_package(
                    FundamentalFilingPackage.model_validate_json(row[0])
                )
            except (duckdb.Error, ValidationError, ValueError) as exc:
                raise StorageError(
                    "Unable to read active DuckDB filing package"
                ) from exc

    def get_filing_package(
        self,
        package_id: str,
        *,
        scope: FundamentalFilingRepositoryScope,
    ) -> FundamentalFilingPackage | None:
        self._validate_filing_identifier(
            package_id,
            pattern=_FILING_PACKAGE_ID_PATTERN,
            label="filing package ID",
        )
        caller_scope = FundamentalFilingRepositoryScope.model_validate(scope)
        scope_fingerprint = self._filing_scope_fingerprint(caller_scope)
        with self._lock:
            self._ensure_open()
            try:
                row = self._connection.execute(
                    "SELECT payload_json "
                    "FROM jarvis_fundamental_filing_packages "
                    "WHERE package_id = ? AND scope_fingerprint = ?",
                    [package_id, scope_fingerprint],
                ).fetchone()
                if row is None:
                    return None
                return self._copy_filing_package(
                    FundamentalFilingPackage.model_validate_json(row[0])
                )
            except (duckdb.Error, ValidationError, ValueError) as exc:
                raise StorageError(
                    "Unable to read DuckDB filing package"
                ) from exc

    def delete_filing_artifact(
        self,
        artifact_id: str,
        *,
        scope: FundamentalFilingRepositoryScope,
        authorization: FundamentalFilingDeleteAuthorization,
    ) -> FundamentalFilingDeleteReceipt:
        self._validate_filing_identifier(
            artifact_id,
            pattern=_FILING_ARTIFACT_ID_PATTERN,
            label="filing artifact ID",
        )
        caller_scope = FundamentalFilingRepositoryScope.model_validate(scope)
        approved = self._validate_filing_delete_authorization(
            authorization,
            target_kind=FundamentalFilingDeleteTarget.ARTIFACT,
            target_id=artifact_id,
        )
        completed_at = self._now()
        self._require_filing_authorization_not_future(
            approved,
            as_of=completed_at,
        )
        scope_fingerprint = self._filing_scope_fingerprint(caller_scope)
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                prior = self._read_filing_delete_receipt_locked(approved)
                if prior is not None:
                    self._connection.execute("COMMIT")
                    return prior
                exists = self._connection.execute(
                    "SELECT 1 FROM jarvis_fundamental_filing_artifacts "
                    "WHERE artifact_id = ? AND scope_fingerprint = ?",
                    [artifact_id, scope_fingerprint],
                ).fetchone()
                if exists is None:
                    status = FundamentalFilingDeleteStatus.NOT_FOUND
                    detail = None
                else:
                    referenced = self._connection.execute(
                        "SELECT 1 FROM "
                        "jarvis_fundamental_filing_package_artifacts "
                        "WHERE artifact_id = ? LIMIT 1",
                        [artifact_id],
                    ).fetchone()
                    if referenced is not None:
                        status = (
                            FundamentalFilingDeleteStatus
                            .REJECTED_ACTIVE_REFERENCE
                        )
                        detail = _FILING_ACTIVE_REFERENCE_DETAIL
                    else:
                        self._connection.execute(
                            "DELETE FROM jarvis_fundamental_filing_artifacts "
                            "WHERE artifact_id = ? AND scope_fingerprint = ?",
                            [artifact_id, scope_fingerprint],
                        )
                        status = FundamentalFilingDeleteStatus.DELETED
                        detail = None
                receipt = FundamentalFilingDeleteReceipt(
                    authorization=approved,
                    status=status,
                    completed_at=completed_at,
                    sanitized_detail=detail,
                )
                self._insert_filing_delete_receipt_locked(receipt)
                self._connection.execute("COMMIT")
                return self._copy_filing_delete_receipt(receipt)
            except (StorageConflictError, StorageError):
                if transaction_started:
                    self._rollback_safely()
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to delete DuckDB filing artifact metadata"
                ) from exc

    def delete_filing_package(
        self,
        package_id: str,
        *,
        scope: FundamentalFilingRepositoryScope,
        authorization: FundamentalFilingDeleteAuthorization,
    ) -> FundamentalFilingDeleteReceipt:
        self._validate_filing_identifier(
            package_id,
            pattern=_FILING_PACKAGE_ID_PATTERN,
            label="filing package ID",
        )
        caller_scope = FundamentalFilingRepositoryScope.model_validate(scope)
        approved = self._validate_filing_delete_authorization(
            authorization,
            target_kind=FundamentalFilingDeleteTarget.PACKAGE,
            target_id=package_id,
        )
        completed_at = self._now()
        self._require_filing_authorization_not_future(
            approved,
            as_of=completed_at,
        )
        scope_fingerprint = self._filing_scope_fingerprint(caller_scope)
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                prior = self._read_filing_delete_receipt_locked(approved)
                if prior is not None:
                    self._connection.execute("COMMIT")
                    return prior
                exists = self._connection.execute(
                    "SELECT 1 FROM jarvis_fundamental_filing_packages "
                    "WHERE package_id = ? AND scope_fingerprint = ?",
                    [package_id, scope_fingerprint],
                ).fetchone()
                status = FundamentalFilingDeleteStatus.NOT_FOUND
                if exists is not None:
                    self._connection.execute(
                        "DELETE FROM "
                        "jarvis_active_fundamental_filing_packages "
                        "WHERE scope_fingerprint = ? AND package_id = ?",
                        [scope_fingerprint, package_id],
                    )
                    self._connection.execute(
                        "DELETE FROM "
                        "jarvis_fundamental_filing_package_artifacts "
                        "WHERE package_id = ?",
                        [package_id],
                    )
                    self._connection.execute(
                        "DELETE FROM jarvis_fundamental_filing_packages "
                        "WHERE package_id = ? AND scope_fingerprint = ?",
                        [package_id, scope_fingerprint],
                    )
                    status = FundamentalFilingDeleteStatus.DELETED
                receipt = FundamentalFilingDeleteReceipt(
                    authorization=approved,
                    status=status,
                    completed_at=completed_at,
                )
                self._insert_filing_delete_receipt_locked(receipt)
                self._connection.execute("COMMIT")
                return self._copy_filing_delete_receipt(receipt)
            except (StorageConflictError, StorageError):
                if transaction_started:
                    self._rollback_safely()
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to delete DuckDB filing package metadata"
                ) from exc

    def find_similar_debate_runs(
        self,
        signature: tuple[str, ...],
        *,
        exclude_run_id: str | None = None,
        limit: int = 5,
    ) -> tuple[DebateRunSummary, ...]:
        if not signature:
            return ()
        placeholders = ", ".join("?" for _ in signature)
        parameters: list[object] = list(signature)
        exclude_clause = ""
        if exclude_run_id is not None:
            exclude_clause = " AND s.run_id != ?"
            parameters.append(exclude_run_id)
        sql = (
            "SELECT r.summary_json, COUNT(*) AS overlap "
            "FROM jarvis_debate_signal_signature AS s "
            "JOIN jarvis_debate_runs AS r ON r.run_id = s.run_id "
            f"WHERE s.token IN ({placeholders}){exclude_clause} "
            "GROUP BY r.run_id, r.summary_json, r.stored_at "
            "ORDER BY overlap DESC, r.stored_at DESC "
            "LIMIT ?"
        )
        parameters.append(limit)
        with self._lock:
            self._ensure_open()
            try:
                rows = self._connection.execute(sql, parameters).fetchall()
                return tuple(
                    DebateRunSummary.model_validate_json(row[0])
                    for row in rows
                )
            except (duckdb.Error, ValidationError, ValueError) as exc:
                raise StorageError(
                    "Unable to find similar DuckDB debate runs"
                ) from exc

    def _initialize_schema(self) -> None:
        with self._lock:
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS jarvis_storage_metadata (
                        metadata_key VARCHAR PRIMARY KEY,
                        metadata_value VARCHAR NOT NULL
                    )
                    """
                )
                row = self._connection.execute(
                    "SELECT metadata_value FROM jarvis_storage_metadata "
                    "WHERE metadata_key = ?",
                    ["schema_version"],
                ).fetchone()
                try:
                    current_version = (
                        int(row[0]) if row is not None else None
                    )
                except (TypeError, ValueError) as exc:
                    raise StorageError(
                        "Invalid DuckDB storage schema version"
                    ) from exc
                if current_version not in (
                    None,
                    1,
                    2,
                    3,
                    4,
                    5,
                    6,
                    7,
                    8,
                    _SCHEMA_VERSION,
                ):
                    raise StorageError(
                        "Unsupported DuckDB storage schema version: "
                        f"{row[0]}"
                    )
                self._create_tables()
                self._create_normalized_tables()
                self._create_debate_tables()
                self._create_fundamental_tables()
                self._create_fundamental_detail_tables()
                self._create_structured_document_tables()
                self._create_fundamental_package_tables()
                self._create_fundamental_filing_tables()
                if current_version == 1:
                    self._backfill_normalized_schema()
                if row is None:
                    self._connection.execute(
                        "INSERT INTO jarvis_storage_metadata VALUES (?, ?)",
                        ["schema_version", str(_SCHEMA_VERSION)],
                    )
                else:
                    self._connection.execute(
                        "UPDATE jarvis_storage_metadata "
                        "SET metadata_value = ? WHERE metadata_key = ?",
                        [str(_SCHEMA_VERSION), "schema_version"],
                    )
                self._connection.execute("COMMIT")
            except Exception:
                if transaction_started:
                    self._rollback_safely()
                raise

    def _create_tables(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jarvis_market_series (
                dataset_id VARCHAR PRIMARY KEY,
                payload_fingerprint VARCHAR NOT NULL,
                stored_at TIMESTAMPTZ NOT NULL,
                exchange VARCHAR NOT NULL,
                symbol_token VARCHAR NOT NULL,
                interval VARCHAR NOT NULL,
                source VARCHAR NOT NULL,
                summary_json VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS jarvis_market_series_lookup
            ON jarvis_market_series (
                symbol_token,
                interval,
                stored_at
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jarvis_backtest_runs (
                run_id VARCHAR PRIMARY KEY,
                result_fingerprint VARCHAR NOT NULL,
                market_fingerprint VARCHAR NOT NULL,
                stored_at TIMESTAMPTZ NOT NULL,
                backtest_id VARCHAR NOT NULL,
                engine_id VARCHAR NOT NULL,
                exchange VARCHAR NOT NULL,
                symbol_token VARCHAR NOT NULL,
                interval VARCHAR NOT NULL,
                summary_json VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS jarvis_backtest_runs_lookup
            ON jarvis_backtest_runs (
                symbol_token,
                interval,
                stored_at
            )
            """
        )

    def _create_debate_tables(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jarvis_debate_runs (
                run_id VARCHAR PRIMARY KEY,
                result_fingerprint VARCHAR NOT NULL,
                technical_fingerprint VARCHAR NOT NULL,
                stored_at TIMESTAMPTZ NOT NULL,
                exchange VARCHAR NOT NULL,
                symbol_token VARCHAR NOT NULL,
                interval VARCHAR NOT NULL,
                winner VARCHAR NOT NULL,
                summary_json VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS jarvis_debate_runs_lookup
            ON jarvis_debate_runs (
                symbol_token,
                interval,
                stored_at
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jarvis_debate_signal_signature (
                run_id VARCHAR NOT NULL,
                token VARCHAR NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS jarvis_debate_signal_signature_token
            ON jarvis_debate_signal_signature (token)
            """
        )

    def _create_fundamental_tables(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jarvis_fundamental_snapshots (
                cache_entry_id VARCHAR PRIMARY KEY,
                cache_key_fingerprint VARCHAR NOT NULL UNIQUE,
                storage_fingerprint VARCHAR NOT NULL,
                tenant_id VARCHAR NOT NULL,
                provider_connection_id VARCHAR NOT NULL,
                provider VARCHAR NOT NULL,
                capability VARCHAR NOT NULL,
                exchange VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                isin VARCHAR,
                provider_company_id VARCHAR,
                provider_slug VARCHAR,
                as_of_date DATE,
                request_max_periods BIGINT,
                request_quarters BIGINT,
                include_promoter_pledge BOOLEAN,
                request_id VARCHAR NOT NULL,
                request_fingerprint VARCHAR NOT NULL,
                result_fingerprint VARCHAR NOT NULL,
                snapshot_id VARCHAR NOT NULL,
                snapshot_fingerprint VARCHAR NOT NULL,
                retrieval_status VARCHAR NOT NULL,
                evidence_posture VARCHAR NOT NULL,
                validation_status VARCHAR NOT NULL,
                retrieved_at TIMESTAMPTZ NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                stored_at TIMESTAMPTZ NOT NULL,
                source_count BIGINT NOT NULL,
                fact_count BIGINT NOT NULL,
                conflict_count BIGINT NOT NULL,
                summary_json VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def _create_fundamental_detail_tables(self) -> None:
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS jarvis_fundamental_scope_lookup
            ON jarvis_fundamental_snapshots (
                tenant_id,
                provider_connection_id,
                provider,
                retrieved_at
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS jarvis_fundamental_issuer_lookup
            ON jarvis_fundamental_snapshots (
                tenant_id,
                provider_connection_id,
                exchange,
                symbol,
                capability,
                retrieved_at
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS jarvis_fundamental_expiry_lookup
            ON jarvis_fundamental_snapshots (expires_at)
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jarvis_fundamental_request_statements (
                cache_entry_id VARCHAR NOT NULL,
                statement_index BIGINT NOT NULL,
                statement VARCHAR NOT NULL,
                PRIMARY KEY (cache_entry_id, statement_index),
                UNIQUE (cache_entry_id, statement)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
            jarvis_fundamental_request_period_types (
                cache_entry_id VARCHAR NOT NULL,
                period_type_index BIGINT NOT NULL,
                period_type VARCHAR NOT NULL,
                PRIMARY KEY (cache_entry_id, period_type_index),
                UNIQUE (cache_entry_id, period_type)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jarvis_fundamental_sources (
                cache_entry_id VARCHAR NOT NULL,
                source_index BIGINT NOT NULL,
                source_id VARCHAR NOT NULL,
                source_type VARCHAR NOT NULL,
                source_rank VARCHAR NOT NULL,
                as_of_date DATE,
                published_at TIMESTAMPTZ,
                retrieved_at TIMESTAMPTZ NOT NULL,
                freshness_status VARCHAR NOT NULL,
                validation_status VARCHAR NOT NULL,
                content_fingerprint VARCHAR NOT NULL,
                source_json VARCHAR NOT NULL,
                PRIMARY KEY (cache_entry_id, source_index),
                UNIQUE (cache_entry_id, source_id)
            )
            """
        )
        # DuckDB can leave this nullable, multi-column ART index unable to
        # delete normalized source rows after a database reopen. Refresh then
        # fails before the replacement transaction can insert newer evidence.
        # Source scans are bounded by each snapshot's small source inventory,
        # so retain the integrity indexes and remove this optional lookup.
        self._connection.execute(
            "DROP INDEX IF EXISTS jarvis_fundamental_source_lookup"
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jarvis_fundamental_facts (
                cache_entry_id VARCHAR NOT NULL,
                fact_index BIGINT NOT NULL,
                evidence_id VARCHAR NOT NULL,
                statement VARCHAR NOT NULL,
                line_item_id VARCHAR NOT NULL,
                line_item_standard VARCHAR NOT NULL,
                period_label VARCHAR NOT NULL,
                period_type VARCHAR NOT NULL,
                period_start DATE,
                period_end DATE,
                value_kind VARCHAR NOT NULL,
                normalized_value VARCHAR,
                currency VARCHAR,
                normalized_unit VARCHAR,
                availability_status VARCHAR NOT NULL,
                evidence_label VARCHAR NOT NULL,
                confidence VARCHAR NOT NULL,
                freshness_status VARCHAR NOT NULL,
                validation_status VARCHAR NOT NULL,
                conflict_status VARCHAR NOT NULL,
                fact_json VARCHAR NOT NULL,
                PRIMARY KEY (cache_entry_id, fact_index),
                UNIQUE (cache_entry_id, evidence_id)
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS jarvis_fundamental_fact_lookup
            ON jarvis_fundamental_facts (
                statement,
                line_item_id,
                period_type,
                period_end
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jarvis_fundamental_conflicts (
                cache_entry_id VARCHAR NOT NULL,
                conflict_index BIGINT NOT NULL,
                conflict_id VARCHAR NOT NULL,
                conflict_type VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                material BOOLEAN NOT NULL,
                working_evidence_id VARCHAR,
                conflict_json VARCHAR NOT NULL,
                PRIMARY KEY (cache_entry_id, conflict_index),
                UNIQUE (cache_entry_id, conflict_id)
            )
            """
        )

    def _create_structured_document_tables(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jarvis_structured_financial_documents (
                cache_entry_id VARCHAR PRIMARY KEY,
                cache_key_fingerprint VARCHAR NOT NULL UNIQUE,
                storage_fingerprint VARCHAR NOT NULL,
                tenant_id VARCHAR NOT NULL,
                provider_connection_id VARCHAR NOT NULL,
                provider VARCHAR NOT NULL,
                exchange VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                isin VARCHAR,
                provider_company_id VARCHAR,
                provider_slug VARCHAR,
                as_of_date DATE,
                document_type VARCHAR NOT NULL,
                reporting_basis VARCHAR NOT NULL,
                request_id VARCHAR NOT NULL,
                request_fingerprint VARCHAR NOT NULL,
                result_fingerprint VARCHAR NOT NULL,
                document_fingerprint VARCHAR NOT NULL,
                retrieved_at TIMESTAMPTZ NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                stored_at TIMESTAMPTZ NOT NULL,
                period_count BIGINT NOT NULL,
                row_count BIGINT NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS jarvis_structured_document_scope_lookup
            ON jarvis_structured_financial_documents (
                tenant_id,
                provider_connection_id,
                provider,
                exchange,
                symbol,
                document_type,
                reporting_basis,
                retrieved_at
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS jarvis_structured_document_expiry_lookup
            ON jarvis_structured_financial_documents (expires_at)
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jarvis_peer_comparisons (
                cache_entry_id VARCHAR PRIMARY KEY,
                cache_key_fingerprint VARCHAR NOT NULL UNIQUE,
                storage_fingerprint VARCHAR NOT NULL,
                tenant_id VARCHAR NOT NULL,
                provider_connection_id VARCHAR NOT NULL,
                provider VARCHAR NOT NULL,
                exchange VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                isin VARCHAR,
                provider_company_id VARCHAR,
                provider_slug VARCHAR,
                as_of_date DATE,
                document_type VARCHAR NOT NULL,
                request_id VARCHAR NOT NULL,
                request_fingerprint VARCHAR NOT NULL,
                result_fingerprint VARCHAR NOT NULL,
                document_fingerprint VARCHAR NOT NULL,
                observation_date DATE NOT NULL,
                retrieved_at TIMESTAMPTZ NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                stored_at TIMESTAMPTZ NOT NULL,
                metric_count BIGINT NOT NULL,
                peer_count BIGINT NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS jarvis_peer_comparison_scope_lookup
            ON jarvis_peer_comparisons (
                tenant_id,
                provider_connection_id,
                provider,
                exchange,
                symbol,
                observation_date,
                retrieved_at
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS jarvis_peer_comparison_fingerprint_lookup
            ON jarvis_peer_comparisons (document_fingerprint)
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS jarvis_peer_comparison_expiry_lookup
            ON jarvis_peer_comparisons (expires_at)
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jarvis_benchmarking_financials (
                cache_entry_id VARCHAR PRIMARY KEY,
                cache_key_fingerprint VARCHAR NOT NULL UNIQUE,
                storage_fingerprint VARCHAR NOT NULL,
                tenant_id VARCHAR NOT NULL,
                provider_connection_id VARCHAR NOT NULL,
                provider VARCHAR NOT NULL,
                exchange VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                isin VARCHAR,
                provider_company_id VARCHAR,
                provider_slug VARCHAR,
                as_of_date DATE,
                document_type VARCHAR NOT NULL,
                request_id VARCHAR NOT NULL,
                request_fingerprint VARCHAR NOT NULL,
                result_fingerprint VARCHAR NOT NULL,
                document_fingerprint VARCHAR NOT NULL,
                observation_date DATE NOT NULL,
                retrieved_at TIMESTAMPTZ NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                stored_at TIMESTAMPTZ NOT NULL,
                company_count BIGINT NOT NULL,
                row_count BIGINT NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS jarvis_benchmarking_financials_scope_lookup
            ON jarvis_benchmarking_financials (
                tenant_id,
                provider_connection_id,
                provider,
                exchange,
                symbol,
                observation_date,
                retrieved_at
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            jarvis_benchmarking_financials_fingerprint_lookup
            ON jarvis_benchmarking_financials (document_fingerprint)
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS jarvis_benchmarking_financials_expiry_lookup
            ON jarvis_benchmarking_financials (expires_at)
            """
        )

    def _create_fundamental_package_tables(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jarvis_fundamental_packages (
                package_id VARCHAR PRIMARY KEY,
                aggregate_fingerprint VARCHAR NOT NULL,
                manifest_fingerprint VARCHAR NOT NULL,
                scope_fingerprint VARCHAR NOT NULL,
                tenant_id VARCHAR NOT NULL,
                provider_connection_id VARCHAR NOT NULL,
                provider VARCHAR NOT NULL,
                exchange VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                operation_id VARCHAR NOT NULL,
                committed_at TIMESTAMPTZ NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS jarvis_fundamental_package_scope_lookup
            ON jarvis_fundamental_packages (
                tenant_id,
                provider_connection_id,
                provider,
                exchange,
                symbol,
                committed_at
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS jarvis_fundamental_package_expiry_lookup
            ON jarvis_fundamental_packages (expires_at)
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jarvis_active_fundamental_packages (
                scope_fingerprint VARCHAR PRIMARY KEY,
                package_id VARCHAR NOT NULL UNIQUE,
                tenant_id VARCHAR NOT NULL,
                provider_connection_id VARCHAR NOT NULL,
                provider VARCHAR NOT NULL,
                exchange VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            )
            """
        )

    def _create_fundamental_filing_tables(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jarvis_fundamental_filing_artifacts (
                artifact_id VARCHAR PRIMARY KEY,
                artifact_fingerprint VARCHAR NOT NULL,
                content_sha256 VARCHAR NOT NULL,
                scope_fingerprint VARCHAR NOT NULL,
                tenant_id VARCHAR NOT NULL,
                provider_connection_id VARCHAR NOT NULL,
                provider VARCHAR NOT NULL,
                exchange VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                isin VARCHAR,
                filing_reference_id VARCHAR NOT NULL,
                category VARCHAR NOT NULL,
                safe_relative_path VARCHAR NOT NULL,
                downloaded_at TIMESTAMPTZ NOT NULL,
                last_checked_at TIMESTAMPTZ NOT NULL,
                recheck_after TIMESTAMPTZ NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS jarvis_filing_artifact_scope_lookup
            ON jarvis_fundamental_filing_artifacts (
                tenant_id,
                provider_connection_id,
                provider,
                exchange,
                symbol,
                filing_reference_id,
                downloaded_at
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS jarvis_filing_artifact_hash_lookup
            ON jarvis_fundamental_filing_artifacts (
                scope_fingerprint,
                content_sha256
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS jarvis_filing_artifact_recheck_lookup
            ON jarvis_fundamental_filing_artifacts (recheck_after)
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jarvis_fundamental_filing_packages (
                package_id VARCHAR PRIMARY KEY,
                manifest_fingerprint VARCHAR NOT NULL,
                discovery_fingerprint VARCHAR NOT NULL,
                scope_fingerprint VARCHAR NOT NULL,
                tenant_id VARCHAR NOT NULL,
                provider_connection_id VARCHAR NOT NULL,
                provider VARCHAR NOT NULL,
                exchange VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                isin VARCHAR,
                operation_id VARCHAR NOT NULL,
                committed_at TIMESTAMPTZ NOT NULL,
                recheck_after TIMESTAMPTZ NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS jarvis_filing_package_scope_lookup
            ON jarvis_fundamental_filing_packages (
                tenant_id,
                provider_connection_id,
                provider,
                exchange,
                symbol,
                committed_at
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS jarvis_filing_package_recheck_lookup
            ON jarvis_fundamental_filing_packages (recheck_after)
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
                jarvis_fundamental_filing_package_artifacts (
                    package_id VARCHAR NOT NULL,
                    artifact_id VARCHAR NOT NULL
                )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS jarvis_filing_package_artifact_lookup
            ON jarvis_fundamental_filing_package_artifacts (artifact_id)
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS jarvis_filing_package_relation_lookup
            ON jarvis_fundamental_filing_package_artifacts (package_id)
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
                jarvis_active_fundamental_filing_packages (
                    scope_fingerprint VARCHAR PRIMARY KEY,
                    package_id VARCHAR NOT NULL UNIQUE,
                    tenant_id VARCHAR NOT NULL,
                    provider_connection_id VARCHAR NOT NULL,
                    provider VARCHAR NOT NULL,
                    exchange VARCHAR NOT NULL,
                    symbol VARCHAR NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
                jarvis_fundamental_filing_delete_receipts (
                    audit_id VARCHAR PRIMARY KEY,
                    operation_id VARCHAR NOT NULL,
                    authorized_by VARCHAR NOT NULL,
                    target_kind VARCHAR NOT NULL,
                    target_id VARCHAR NOT NULL,
                    status VARCHAR NOT NULL,
                    authorized_at TIMESTAMPTZ NOT NULL,
                    completed_at TIMESTAMPTZ NOT NULL,
                    payload_json VARCHAR NOT NULL
                )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS jarvis_filing_delete_target_lookup
            ON jarvis_fundamental_filing_delete_receipts (
                target_kind,
                target_id,
                completed_at
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jarvis_fundamental_filing_evidence (
                record_id VARCHAR PRIMARY KEY,
                record_fingerprint VARCHAR NOT NULL,
                scope_fingerprint VARCHAR NOT NULL,
                tenant_id VARCHAR NOT NULL,
                provider_connection_id VARCHAR NOT NULL,
                provider VARCHAR NOT NULL,
                exchange VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                isin VARCHAR,
                artifact_id VARCHAR NOT NULL,
                artifact_fingerprint VARCHAR NOT NULL,
                extraction_policy_fingerprint VARCHAR NOT NULL,
                validation_policy_fingerprint VARCHAR NOT NULL,
                extraction_fingerprint VARCHAR NOT NULL,
                validation_fingerprint VARCHAR NOT NULL,
                releaseable BOOLEAN NOT NULL,
                extracted_at TIMESTAMPTZ NOT NULL,
                stored_at TIMESTAMPTZ NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS jarvis_filing_evidence_scope_lookup
            ON jarvis_fundamental_filing_evidence (
                tenant_id,
                provider_connection_id,
                provider,
                exchange,
                symbol,
                artifact_id,
                stored_at
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS jarvis_filing_evidence_policy_lookup
            ON jarvis_fundamental_filing_evidence (
                scope_fingerprint,
                artifact_id,
                artifact_fingerprint,
                extraction_policy_fingerprint,
                validation_policy_fingerprint,
                extracted_at,
                stored_at
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS jarvis_filing_evidence_lineage_lookup
            ON jarvis_fundamental_filing_evidence (
                scope_fingerprint,
                extraction_fingerprint,
                validation_fingerprint
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jarvis_corporate_filings_analysis (
                record_id VARCHAR PRIMARY KEY,
                record_fingerprint VARCHAR NOT NULL,
                scope_fingerprint VARCHAR NOT NULL,
                tenant_id VARCHAR NOT NULL,
                provider_connection_id VARCHAR NOT NULL,
                provider VARCHAR NOT NULL,
                exchange VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                isin VARCHAR,
                fiscal_year BIGINT NOT NULL,
                fiscal_quarter BIGINT NOT NULL,
                analysis_kind VARCHAR NOT NULL,
                period_key_fingerprint VARCHAR NOT NULL,
                reuse_key_fingerprint VARCHAR NOT NULL,
                request_fingerprint VARCHAR NOT NULL,
                filing_package_id VARCHAR NOT NULL,
                filing_package_fingerprint VARCHAR NOT NULL,
                bundle_fingerprint VARCHAR NOT NULL,
                policy_fingerprint VARCHAR NOT NULL,
                skill_id VARCHAR NOT NULL,
                skill_version VARCHAR NOT NULL,
                skill_sha256 VARCHAR NOT NULL,
                model_configuration_fingerprint VARCHAR NOT NULL,
                result_fingerprint VARCHAR NOT NULL,
                analysis_completed_at TIMESTAMPTZ NOT NULL,
                stored_at TIMESTAMPTZ NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
                jarvis_corporate_analysis_reuse_lookup
            ON jarvis_corporate_filings_analysis (
                scope_fingerprint,
                reuse_key_fingerprint,
                stored_at
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
                jarvis_corporate_analysis_period_history_lookup
            ON jarvis_corporate_filings_analysis (
                scope_fingerprint,
                period_key_fingerprint,
                stored_at,
                analysis_completed_at
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
                jarvis_corporate_analysis_lineage_lookup
            ON jarvis_corporate_filings_analysis (
                filing_package_fingerprint,
                bundle_fingerprint,
                policy_fingerprint,
                model_configuration_fingerprint
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
                jarvis_active_corporate_filings_analysis (
                    scope_fingerprint VARCHAR NOT NULL,
                    period_key_fingerprint VARCHAR NOT NULL,
                    record_id VARCHAR NOT NULL,
                    activated_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (scope_fingerprint, period_key_fingerprint)
                )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
                jarvis_active_corporate_analysis_record_lookup
            ON jarvis_active_corporate_filings_analysis (record_id)
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
                jarvis_corporate_filings_analysis_active_history (
                    scope_fingerprint VARCHAR NOT NULL,
                    period_key_fingerprint VARCHAR NOT NULL,
                    activation_sequence BIGINT NOT NULL,
                    record_id VARCHAR NOT NULL,
                    activated_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (
                        scope_fingerprint,
                        period_key_fingerprint,
                        activation_sequence
                    )
                )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
                jarvis_corporate_analysis_activation_history_lookup
            ON jarvis_corporate_filings_analysis_active_history (
                scope_fingerprint,
                period_key_fingerprint,
                activated_at,
                activation_sequence
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
                jarvis_corporate_filings_analysis_delete_receipts (
                    audit_id VARCHAR PRIMARY KEY,
                    operation_id VARCHAR NOT NULL,
                    authorized_by VARCHAR NOT NULL,
                    record_id VARCHAR NOT NULL,
                    record_fingerprint VARCHAR NOT NULL,
                    status VARCHAR NOT NULL,
                    authorized_at TIMESTAMPTZ NOT NULL,
                    completed_at TIMESTAMPTZ NOT NULL,
                    payload_json VARCHAR NOT NULL
                )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
                jarvis_corporate_analysis_delete_target_lookup
            ON jarvis_corporate_filings_analysis_delete_receipts (
                record_id,
                completed_at
            )
            """
        )

    def _create_normalized_tables(self) -> None:
        self._connection.execute(
            "ALTER TABLE jarvis_market_series ADD COLUMN IF NOT EXISTS "
            "instrument_id VARCHAR"
        )
        self._connection.execute(
            "ALTER TABLE jarvis_market_series ADD COLUMN IF NOT EXISTS "
            "symbol VARCHAR"
        )
        self._connection.execute(
            "ALTER TABLE jarvis_backtest_runs ADD COLUMN IF NOT EXISTS "
            "market_dataset_id VARCHAR"
        )
        self._connection.execute(
            "ALTER TABLE jarvis_backtest_runs ADD COLUMN IF NOT EXISTS "
            "instrument_id VARCHAR"
        )
        self._connection.execute(
            "ALTER TABLE jarvis_backtest_runs ADD COLUMN IF NOT EXISTS "
            "symbol VARCHAR"
        )
        self._connection.execute(
            "ALTER TABLE jarvis_backtest_runs ADD COLUMN IF NOT EXISTS "
            "source VARCHAR"
        )
        self._connection.execute(
            "ALTER TABLE jarvis_backtest_runs ADD COLUMN IF NOT EXISTS "
            "strategy_configuration_id VARCHAR"
        )
        self._connection.execute(
            "ALTER TABLE jarvis_backtest_runs ADD COLUMN IF NOT EXISTS "
            "walk_forward_config_json VARCHAR"
        )
        self._connection.execute(
            "ALTER TABLE jarvis_backtest_runs ADD COLUMN IF NOT EXISTS "
            "resolved_warmup_candles BIGINT"
        )
        self._connection.execute(
            "ALTER TABLE jarvis_backtest_runs ADD COLUMN IF NOT EXISTS "
            "payload_schema_version BIGINT"
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jarvis_instruments (
                instrument_id VARCHAR PRIMARY KEY,
                source VARCHAR NOT NULL,
                exchange VARCHAR NOT NULL,
                symbol_token VARCHAR NOT NULL,
                current_symbol VARCHAR NOT NULL,
                first_seen_at TIMESTAMPTZ NOT NULL,
                last_seen_at TIMESTAMPTZ NOT NULL,
                UNIQUE (source, exchange, symbol_token)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jarvis_instrument_symbols (
                instrument_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                observed_at TIMESTAMPTZ NOT NULL,
                dataset_id VARCHAR NOT NULL,
                PRIMARY KEY (instrument_id, symbol, dataset_id)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jarvis_market_candles (
                dataset_id VARCHAR NOT NULL,
                candle_index BIGINT NOT NULL,
                candle_at TIMESTAMPTZ NOT NULL,
                open DOUBLE NOT NULL,
                high DOUBLE NOT NULL,
                low DOUBLE NOT NULL,
                close DOUBLE NOT NULL,
                volume BIGINT NOT NULL,
                PRIMARY KEY (dataset_id, candle_index),
                UNIQUE (dataset_id, candle_at)
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS jarvis_market_candles_time_lookup
            ON jarvis_market_candles (dataset_id, candle_at)
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jarvis_strategy_configurations (
                strategy_configuration_id VARCHAR PRIMARY KEY,
                configuration_fingerprint VARCHAR NOT NULL UNIQUE,
                strategy_id VARCHAR NOT NULL,
                technical_agent_id VARCHAR NOT NULL,
                technical_evaluator_id VARCHAR NOT NULL,
                technical_configuration_fingerprint VARCHAR NOT NULL,
                trade_planning_agent_id VARCHAR NOT NULL,
                trade_planner_id VARCHAR NOT NULL,
                trade_planning_configuration_fingerprint VARCHAR NOT NULL,
                execution_engine_id VARCHAR NOT NULL,
                execution_configuration_fingerprint VARCHAR NOT NULL,
                walk_forward_configuration_fingerprint VARCHAR NOT NULL,
                configuration_json VARCHAR NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jarvis_strategy_weights (
                strategy_configuration_id VARCHAR NOT NULL,
                category VARCHAR NOT NULL,
                weight DOUBLE NOT NULL,
                PRIMARY KEY (strategy_configuration_id, category)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jarvis_backtest_evaluations (
                run_id VARCHAR NOT NULL,
                candle_index BIGINT NOT NULL,
                candle_at TIMESTAMPTZ NOT NULL,
                outcome VARCHAR NOT NULL,
                capital_before DOUBLE NOT NULL,
                capital_after DOUBLE,
                technical_submission_id VARCHAR,
                technical_decision_id VARCHAR,
                technical_configuration_fingerprint VARCHAR,
                signal_direction VARCHAR,
                signal_stance VARCHAR,
                signal_score DOUBLE,
                confidence_percentage DOUBLE,
                coverage_percentage DOUBLE,
                agreement_percentage DOUBLE,
                profile_id VARCHAR,
                minimum_coverage_percentage DOUBLE,
                directional_threshold DOUBLE,
                strong_threshold DOUBLE,
                synchronized_evidence_required BOOLEAN,
                rationale VARCHAR,
                planning_submission_id VARCHAR,
                planning_decision_id VARCHAR,
                planning_configuration_fingerprint VARCHAR,
                planning_disposition VARCHAR,
                planning_reason VARCHAR,
                execution_submission_id VARCHAR,
                execution_decision_id VARCHAR,
                execution_configuration_fingerprint VARCHAR,
                execution_outcome VARCHAR,
                active_trade_id VARCHAR,
                message VARCHAR NOT NULL,
                record_json VARCHAR NOT NULL,
                PRIMARY KEY (run_id, candle_index)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
            jarvis_backtest_evaluation_categories (
                run_id VARCHAR NOT NULL,
                candle_index BIGINT NOT NULL,
                category VARCHAR NOT NULL,
                weight DOUBLE NOT NULL,
                category_score DOUBLE NOT NULL,
                weighted_score DOUBLE NOT NULL,
                PRIMARY KEY (run_id, candle_index, category)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jarvis_backtest_signal_evidence (
                run_id VARCHAR NOT NULL,
                candle_index BIGINT NOT NULL,
                evidence_index BIGINT NOT NULL,
                evidence_id VARCHAR NOT NULL,
                name VARCHAR NOT NULL,
                category VARCHAR NOT NULL,
                direction VARCHAR NOT NULL,
                strength VARCHAR NOT NULL,
                source VARCHAR NOT NULL,
                observed_at TIMESTAMPTZ NOT NULL,
                available_at TIMESTAMPTZ NOT NULL,
                provenance VARCHAR NOT NULL,
                explanation VARCHAR NOT NULL,
                observed_values_json VARCHAR NOT NULL,
                parameters_json VARCHAR NOT NULL,
                evidence_json VARCHAR NOT NULL,
                PRIMARY KEY (run_id, candle_index, evidence_index),
                UNIQUE (run_id, candle_index, evidence_id)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
            jarvis_backtest_signal_contributions (
                run_id VARCHAR NOT NULL,
                candle_index BIGINT NOT NULL,
                contribution_index BIGINT NOT NULL,
                evidence_id VARCHAR NOT NULL,
                category VARCHAR NOT NULL,
                direction VARCHAR NOT NULL,
                strength VARCHAR NOT NULL,
                strength_value DOUBLE NOT NULL,
                signed_value DOUBLE NOT NULL,
                category_weight DOUBLE NOT NULL,
                weighted_value DOUBLE NOT NULL,
                contribution_json VARCHAR NOT NULL,
                PRIMARY KEY (run_id, candle_index, contribution_index),
                UNIQUE (run_id, candle_index, evidence_id)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jarvis_backtest_trades (
                run_id VARCHAR NOT NULL,
                trade_id VARCHAR NOT NULL,
                evaluation_index BIGINT NOT NULL,
                planning_submission_id VARCHAR NOT NULL,
                execution_submission_id VARCHAR NOT NULL,
                direction VARCHAR NOT NULL,
                signal_stance VARCHAR NOT NULL,
                signal_score DOUBLE NOT NULL,
                outcome VARCHAR NOT NULL,
                selected_target VARCHAR NOT NULL,
                entry_index BIGINT NOT NULL,
                entry_at TIMESTAMPTZ NOT NULL,
                entry_price DOUBLE NOT NULL,
                stop_loss_price DOUBLE NOT NULL,
                target_price DOUBLE NOT NULL,
                quantity BIGINT NOT NULL,
                exit_index BIGINT,
                exit_at TIMESTAMPTZ,
                exit_price DOUBLE,
                capital_before DOUBLE NOT NULL,
                capital_after DOUBLE,
                gross_pnl DOUBLE,
                entry_fee DOUBLE NOT NULL,
                exit_fee DOUBLE NOT NULL,
                total_costs DOUBLE NOT NULL,
                net_pnl DOUBLE,
                realized_r_multiple DOUBLE,
                bars_held BIGINT NOT NULL,
                record_json VARCHAR NOT NULL,
                PRIMARY KEY (run_id, trade_id)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jarvis_backtest_equity_points (
                run_id VARCHAR NOT NULL,
                candle_index BIGINT NOT NULL,
                point_at TIMESTAMPTZ NOT NULL,
                close DOUBLE NOT NULL,
                equity DOUBLE NOT NULL,
                running_peak DOUBLE NOT NULL,
                drawdown_amount DOUBLE NOT NULL,
                drawdown_percentage DOUBLE NOT NULL,
                active_trade_id VARCHAR,
                PRIMARY KEY (run_id, candle_index)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jarvis_backtest_performance (
                run_id VARCHAR PRIMARY KEY,
                interval VARCHAR NOT NULL,
                initial_capital DOUBLE NOT NULL,
                final_equity DOUBLE NOT NULL,
                net_profit DOUBLE NOT NULL,
                total_return_percentage DOUBLE NOT NULL,
                attempted_evaluations BIGINT NOT NULL,
                technical_failures BIGINT NOT NULL,
                no_trade_evaluations BIGINT NOT NULL,
                skipped_open_position_evaluations BIGINT NOT NULL,
                entered_trades BIGINT NOT NULL,
                closed_trades BIGINT NOT NULL,
                open_trades BIGINT NOT NULL,
                winning_trades BIGINT NOT NULL,
                losing_trades BIGINT NOT NULL,
                breakeven_trades BIGINT NOT NULL,
                win_rate_percentage DOUBLE NOT NULL,
                gross_profit DOUBLE NOT NULL,
                gross_loss DOUBLE NOT NULL,
                profit_factor DOUBLE,
                expectancy_per_closed_trade DOUBLE,
                average_win DOUBLE,
                average_loss DOUBLE,
                payoff_ratio DOUBLE,
                total_costs DOUBLE NOT NULL,
                average_realized_r DOUBLE,
                maximum_drawdown_amount DOUBLE NOT NULL,
                maximum_drawdown_percentage DOUBLE NOT NULL,
                average_bars_held DOUBLE,
                exposure_percentage DOUBLE NOT NULL,
                maximum_consecutive_losses BIGINT NOT NULL,
                performance_json VARCHAR NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
            jarvis_backtest_performance_segments (
                run_id VARCHAR NOT NULL,
                segment_type VARCHAR NOT NULL,
                segment_value VARCHAR NOT NULL,
                entered_trades BIGINT NOT NULL,
                closed_trades BIGINT NOT NULL,
                winning_trades BIGINT NOT NULL,
                losing_trades BIGINT NOT NULL,
                breakeven_trades BIGINT NOT NULL,
                win_rate_percentage DOUBLE NOT NULL,
                net_pnl DOUBLE NOT NULL,
                total_costs DOUBLE NOT NULL,
                average_realized_r DOUBLE,
                PRIMARY KEY (run_id, segment_type, segment_value)
            )
            """
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS jarvis_backtest_instrument_lookup "
            "ON jarvis_backtest_runs (instrument_id, interval, stored_at)"
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS jarvis_backtest_strategy_lookup "
            "ON jarvis_backtest_runs "
            "(strategy_configuration_id, stored_at)"
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS jarvis_evaluation_signal_lookup "
            "ON jarvis_backtest_evaluations "
            "(run_id, signal_direction, signal_stance, candle_at)"
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS jarvis_trade_outcome_lookup "
            "ON jarvis_backtest_trades (run_id, outcome, entry_at)"
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS jarvis_equity_time_lookup "
            "ON jarvis_backtest_equity_points (run_id, point_at)"
        )

    def _backfill_normalized_schema(self) -> None:
        """Populate version-two relations from immutable version-one JSON."""
        market_rows = self._connection.execute(
            "SELECT payload_json FROM jarvis_market_series"
        ).fetchall()
        for (payload_json,) in market_rows:
            stored = StoredMarketSeries.model_validate_json(payload_json)
            self._persist_market_candles(stored)

        backtest_rows = self._connection.execute(
            "SELECT payload_json FROM jarvis_backtest_runs"
        ).fetchall()
        for (payload_json,) in backtest_rows:
            stored = StoredBacktestRun.model_validate_json(payload_json)
            self._persist_backtest_details(stored)

    def _persist_market_candles(
        self,
        stored: StoredMarketSeries,
    ) -> None:
        series = stored.series
        instrument_id = self._persist_instrument(
            source=series.source,
            exchange=series.exchange,
            symbol_token=series.symbol_token,
            symbol=series.symbol,
            observed_at=series.retrieved_at,
        )
        self._connection.execute(
            "UPDATE jarvis_market_series SET instrument_id = ?, symbol = ? "
            "WHERE dataset_id = ?",
            [instrument_id, series.symbol, stored.dataset_id],
        )
        self._connection.execute(
            "INSERT OR IGNORE INTO jarvis_instrument_symbols "
            "(instrument_id, symbol, observed_at, dataset_id) "
            "VALUES (?, ?, ?, ?)",
            [
                instrument_id,
                series.symbol,
                series.retrieved_at,
                stored.dataset_id,
            ],
        )
        self._connection.execute(
            "DELETE FROM jarvis_market_candles WHERE dataset_id = ?",
            [stored.dataset_id],
        )
        if series.candles:
            self._connection.executemany(
                "INSERT INTO jarvis_market_candles VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    [
                        stored.dataset_id,
                        index,
                        candle.timestamp,
                        candle.open,
                        candle.high,
                        candle.low,
                        candle.close,
                        candle.volume,
                    ]
                    for index, candle in enumerate(series.candles)
                ],
            )

    def _persist_instrument(
        self,
        *,
        source: str,
        exchange: str,
        symbol_token: str,
        symbol: str,
        observed_at,
    ) -> str:
        identity = f"{source}|{exchange}|{symbol_token}"
        instrument_id = (
            "instrument:"
            + sha256(identity.encode("utf-8")).hexdigest()
        )
        existing = self._connection.execute(
            "SELECT instrument_id FROM jarvis_instruments "
            "WHERE source = ? AND exchange = ? AND symbol_token = ?",
            [source, exchange, symbol_token],
        ).fetchone()
        if existing is None:
            self._connection.execute(
                "INSERT INTO jarvis_instruments VALUES "
                "(?, ?, ?, ?, ?, ?, ?)",
                [
                    instrument_id,
                    source,
                    exchange,
                    symbol_token,
                    symbol,
                    observed_at,
                    observed_at,
                ],
            )
        else:
            instrument_id = existing[0]
            self._connection.execute(
                "UPDATE jarvis_instruments SET current_symbol = ?, "
                "first_seen_at = LEAST(first_seen_at, ?), "
                "last_seen_at = GREATEST(last_seen_at, ?) "
                "WHERE instrument_id = ?",
                [symbol, observed_at, observed_at, instrument_id],
            )
        return instrument_id

    def _insert_backtest_parent(self, stored: StoredBacktestRun) -> None:
        result = stored.result
        summary = backtest_run_summary(stored)
        series = result.market_series
        self._connection.execute(
            "INSERT INTO jarvis_backtest_runs ("
            "run_id, result_fingerprint, market_fingerprint, stored_at, "
            "backtest_id, engine_id, exchange, symbol_token, interval, "
            "summary_json, payload_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                stored.run_id,
                stored.result_fingerprint,
                stored.market_fingerprint,
                stored.stored_at,
                result.backtest_id,
                result.engine_id,
                series.exchange,
                series.symbol_token,
                series.interval,
                summary.model_dump_json(),
                stored.model_dump_json(),
            ],
        )

    def _persist_backtest_details(
        self,
        stored: StoredBacktestRun,
    ) -> None:
        result = stored.result
        series = result.market_series
        instrument_id = self._persist_instrument(
            source=series.source,
            exchange=series.exchange,
            symbol_token=series.symbol_token,
            symbol=series.symbol,
            observed_at=series.retrieved_at,
        )
        strategy_id = self._persist_strategy_configuration(stored)
        dataset_row = self._connection.execute(
            "SELECT dataset_id FROM jarvis_market_series "
            "WHERE payload_fingerprint = ? ORDER BY stored_at DESC LIMIT 1",
            [stored.market_fingerprint],
        ).fetchone()
        dataset_id = dataset_row[0] if dataset_row else None
        self._connection.execute(
            "UPDATE jarvis_backtest_runs SET market_dataset_id = ?, "
            "instrument_id = ?, symbol = ?, source = ?, "
            "strategy_configuration_id = ?, walk_forward_config_json = ?, "
            "resolved_warmup_candles = ?, payload_schema_version = ? "
            "WHERE run_id = ?",
            [
                dataset_id,
                instrument_id,
                series.symbol,
                series.source,
                strategy_id,
                result.config.model_dump_json(),
                result.resolved_warmup_candles,
                stored.storage_schema_version,
                stored.run_id,
            ],
        )
        for table in (
            "jarvis_backtest_evaluation_categories",
            "jarvis_backtest_signal_evidence",
            "jarvis_backtest_signal_contributions",
            "jarvis_backtest_evaluations",
            "jarvis_backtest_trades",
            "jarvis_backtest_equity_points",
            "jarvis_backtest_performance_segments",
            "jarvis_backtest_performance",
        ):
            self._connection.execute(
                f"DELETE FROM {table} WHERE run_id = ?",
                [stored.run_id],
            )
        self._persist_evaluations(stored)
        self._persist_trades(stored)
        self._persist_equity_curve(stored)
        self._persist_performance(stored)

    def _persist_strategy_configuration(
        self,
        stored: StoredBacktestRun,
    ) -> str | None:
        configuration = stored.result.strategy_configuration
        if configuration is None:
            return None
        strategy_id = configuration.strategy_configuration_id
        payload_json = configuration.model_dump_json()
        existing = self._connection.execute(
            "SELECT configuration_json FROM jarvis_strategy_configurations "
            "WHERE strategy_configuration_id = ?",
            [strategy_id],
        ).fetchone()
        if existing is not None and existing[0] != payload_json:
            raise StorageConflictError(
                "strategy configuration identifier already contains "
                "different parameters"
            )
        if existing is None:
            self._connection.execute(
                "INSERT INTO jarvis_strategy_configurations VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    strategy_id,
                    configuration.configuration_fingerprint,
                    configuration.strategy_id,
                    configuration.technical_agent_id,
                    configuration.technical_evaluator_id,
                    configuration.technical_configuration_fingerprint,
                    configuration.trade_planning_agent_id,
                    configuration.trade_planner_id,
                    configuration.trade_planning_configuration_fingerprint,
                    configuration.execution_engine_id,
                    configuration.execution_configuration_fingerprint,
                    configuration.walk_forward_configuration_fingerprint,
                    payload_json,
                    stored.stored_at,
                ],
            )
            self._connection.executemany(
                "INSERT INTO jarvis_strategy_weights VALUES (?, ?, ?)",
                [
                    [strategy_id, category, weight]
                    for category, weight in sorted(
                        configuration.category_weights.items()
                    )
                ],
            )
        return strategy_id

    def _persist_evaluations(self, stored: StoredBacktestRun) -> None:
        for record in stored.result.evaluations:
            profile = record.technical_profile
            values = [
                stored.run_id,
                record.candle_index,
                record.candle.timestamp,
                _enum_value(record.outcome),
                record.capital_before,
                record.capital_after,
                record.technical_submission_id,
                record.technical_decision_id,
                record.technical_configuration_fingerprint,
                _enum_value(record.signal_direction),
                _enum_value(record.signal_stance),
                record.signal_score,
                profile.confidence_percentage if profile else None,
                profile.coverage_percentage if profile else None,
                profile.agreement_percentage if profile else None,
                profile.profile_id if profile else None,
                profile.minimum_coverage_percentage if profile else None,
                profile.directional_threshold if profile else None,
                profile.strong_threshold if profile else None,
                (
                    profile.synchronized_evidence_required
                    if profile
                    else None
                ),
                profile.rationale if profile else None,
                record.planning_submission_id,
                record.planning_decision_id,
                record.planning_configuration_fingerprint,
                _enum_value(record.planning_disposition),
                _enum_value(record.planning_reason),
                record.execution_submission_id,
                record.execution_decision_id,
                record.execution_configuration_fingerprint,
                _enum_value(record.execution_outcome),
                record.active_trade_id,
                record.message,
                record.model_dump_json(),
            ]
            self._connection.execute(
                "INSERT INTO jarvis_backtest_evaluations VALUES ("
                + ", ".join("?" for _ in values)
                + ")",
                values,
            )
            if profile is not None:
                self._persist_profile_details(
                    run_id=stored.run_id,
                    candle_index=record.candle_index,
                    profile=profile,
                )

    def _persist_profile_details(
        self,
        *,
        run_id: str,
        candle_index: int,
        profile,
    ) -> None:
        self._connection.executemany(
            "INSERT INTO jarvis_backtest_evaluation_categories "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                [
                    run_id,
                    candle_index,
                    category,
                    profile.category_weights[category],
                    category_score,
                    category_score * profile.category_weights[category],
                ]
                for category, category_score in sorted(
                    profile.category_scores.items()
                )
            ],
        )
        if profile.snapshot.evidence:
            self._connection.executemany(
                "INSERT INTO jarvis_backtest_signal_evidence VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    [
                        run_id,
                        candle_index,
                        index,
                        evidence.evidence_id,
                        evidence.name,
                        _enum_value(evidence.category),
                        _enum_value(evidence.direction),
                        _enum_value(evidence.strength),
                        evidence.source,
                        evidence.observed_at,
                        evidence.available_at,
                        _enum_value(evidence.provenance),
                        evidence.explanation,
                        json.dumps(
                            evidence.observed_values,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        json.dumps(
                            evidence.parameters,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        evidence.model_dump_json(),
                    ]
                    for index, evidence in enumerate(
                        profile.snapshot.evidence
                    )
                ],
            )
        if profile.contributions:
            self._connection.executemany(
                "INSERT INTO jarvis_backtest_signal_contributions VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    [
                        run_id,
                        candle_index,
                        index,
                        contribution.evidence_id,
                        _enum_value(contribution.category),
                        _enum_value(contribution.direction),
                        _enum_value(contribution.strength),
                        contribution.strength_value,
                        contribution.signed_value,
                        profile.category_weights[
                            contribution.category.value
                        ],
                        contribution.signed_value
                        * profile.category_weights[
                            contribution.category.value
                        ],
                        contribution.model_dump_json(),
                    ]
                    for index, contribution in enumerate(
                        profile.contributions
                    )
                ],
            )

    def _persist_trades(self, stored: StoredBacktestRun) -> None:
        trades = stored.result.trades
        if not trades:
            return
        self._connection.executemany(
            "INSERT INTO jarvis_backtest_trades VALUES ("
            "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, ?, ?, ?, ?, ?) ",
            [
                [
                    stored.run_id,
                    trade.trade_id,
                    trade.evaluation_index,
                    trade.planning_submission_id,
                    trade.execution_submission_id,
                    _enum_value(trade.direction),
                    _enum_value(trade.signal_stance),
                    trade.signal_score,
                    _enum_value(trade.outcome),
                    _enum_value(trade.selected_target),
                    trade.entry_index,
                    trade.entry_at,
                    trade.entry_price,
                    trade.stop_loss_price,
                    trade.target_price,
                    trade.quantity,
                    trade.exit_index,
                    trade.exit_at,
                    trade.exit_price,
                    trade.capital_before,
                    trade.capital_after,
                    trade.gross_pnl,
                    trade.entry_fee,
                    trade.exit_fee,
                    trade.total_costs,
                    trade.net_pnl,
                    trade.realized_r_multiple,
                    trade.bars_held,
                    trade.model_dump_json(),
                ]
                for trade in trades
            ],
        )

    def _persist_equity_curve(self, stored: StoredBacktestRun) -> None:
        points = stored.result.equity_curve
        if not points:
            return
        self._connection.executemany(
            "INSERT INTO jarvis_backtest_equity_points VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                [
                    stored.run_id,
                    point.candle_index,
                    point.timestamp,
                    point.close,
                    point.equity,
                    point.running_peak,
                    point.drawdown_amount,
                    point.drawdown_percentage,
                    point.active_trade_id,
                ]
                for point in points
            ],
        )

    def _persist_performance(self, stored: StoredBacktestRun) -> None:
        performance = stored.result.performance
        scalar_fields = [
            name
            for name in performance.__class__.model_fields
            if name not in {"direction_breakdown", "stance_breakdown"}
        ]
        columns = ["run_id", *scalar_fields, "performance_json"]
        values = [
            stored.run_id,
            *(getattr(performance, name) for name in scalar_fields),
            performance.model_dump_json(),
        ]
        placeholders = ", ".join("?" for _ in columns)
        self._connection.execute(
            f"INSERT INTO jarvis_backtest_performance "
            f"({', '.join(columns)}) VALUES ({placeholders})",
            values,
        )
        segments = []
        for segment_type, breakdown in (
            ("direction", performance.direction_breakdown),
            ("stance", performance.stance_breakdown),
        ):
            for segment_value, segment in breakdown.items():
                segments.append(
                    [
                        stored.run_id,
                        segment_type,
                        _enum_value(segment_value),
                        segment.entered_trades,
                        segment.closed_trades,
                        segment.winning_trades,
                        segment.losing_trades,
                        segment.breakeven_trades,
                        segment.win_rate_percentage,
                        segment.net_pnl,
                        segment.total_costs,
                        segment.average_realized_r,
                    ]
                )
        if segments:
            self._connection.executemany(
                "INSERT INTO jarvis_backtest_performance_segments VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                segments,
            )

    def _now(self) -> datetime:
        return _require_aware_datetime(
            self._clock(),
            "fundamental repository clock",
        )

    def _insert_fundamental_parent(
        self,
        stored: StoredFundamentalSnapshot,
        summary: FundamentalSnapshotSummary,
    ) -> None:
        columns, values = self._fundamental_parent_row(stored, summary)
        self._connection.execute(
            "INSERT INTO jarvis_fundamental_snapshots ("
            + ", ".join(columns)
            + ") VALUES ("
            + ", ".join("?" for _ in values)
            + ")",
            values,
        )

    def _update_fundamental_parent(
        self,
        stored: StoredFundamentalSnapshot,
        summary: FundamentalSnapshotSummary,
    ) -> None:
        columns, values = self._fundamental_parent_row(stored, summary)
        mutable_columns = columns[1:]
        self._connection.execute(
            "UPDATE jarvis_fundamental_snapshots SET "
            + ", ".join(f"{column} = ?" for column in mutable_columns)
            + " WHERE cache_entry_id = ?",
            [*values[1:], values[0]],
        )

    @staticmethod
    def _fundamental_parent_row(
        stored: StoredFundamentalSnapshot,
        summary: FundamentalSnapshotSummary,
    ) -> tuple[tuple[str, ...], list[object]]:
        key = stored.cache_key
        issuer = key.issuer
        columns = (
            "cache_entry_id",
            "cache_key_fingerprint",
            "storage_fingerprint",
            "tenant_id",
            "provider_connection_id",
            "provider",
            "capability",
            "exchange",
            "symbol",
            "isin",
            "provider_company_id",
            "provider_slug",
            "as_of_date",
            "request_max_periods",
            "request_quarters",
            "include_promoter_pledge",
            "request_id",
            "request_fingerprint",
            "result_fingerprint",
            "snapshot_id",
            "snapshot_fingerprint",
            "retrieval_status",
            "evidence_posture",
            "validation_status",
            "retrieved_at",
            "expires_at",
            "stored_at",
            "source_count",
            "fact_count",
            "conflict_count",
            "summary_json",
            "payload_json",
        )
        values = [
            key.cache_entry_id,
            key.cache_key_fingerprint,
            stored.storage_fingerprint,
            key.tenant_id,
            key.provider_connection_id,
            key.provider,
            _enum_value(key.capability),
            issuer.exchange,
            issuer.symbol,
            issuer.isin,
            issuer.provider_company_id,
            issuer.provider_slug,
            key.as_of_date,
            key.max_periods,
            key.quarters,
            key.include_promoter_pledge,
            stored.request.request_id,
            stored.request_fingerprint,
            stored.result_fingerprint,
            summary.snapshot_id,
            stored.snapshot_fingerprint,
            _enum_value(stored.retrieval.status),
            _enum_value(summary.evidence_posture),
            _enum_value(summary.validation_status),
            stored.retrieved_at,
            stored.expires_at,
            stored.stored_at,
            summary.source_count,
            summary.fact_count,
            summary.conflict_count,
            summary.model_dump_json(),
            stored.model_dump_json(exclude_computed_fields=True),
        ]
        return columns, values

    def _persist_fundamental_details(
        self,
        stored: StoredFundamentalSnapshot,
    ) -> None:
        cache_entry_id = stored.cache_key.cache_entry_id
        statements = [
            [cache_entry_id, index, _enum_value(statement)]
            for index, statement in enumerate(stored.cache_key.statements)
        ]
        self._sync_fundamental_detail_rows(
            table="jarvis_fundamental_request_statements",
            index_column="statement_index",
            unique_column="statement",
            mutable_columns=("statement",),
            rows=statements,
            cache_entry_id=cache_entry_id,
            refresh_fingerprint=stored.storage_fingerprint,
        )

        period_types = [
            [cache_entry_id, index, _enum_value(period_type)]
            for index, period_type in enumerate(
                stored.cache_key.period_types
            )
        ]
        self._sync_fundamental_detail_rows(
            table="jarvis_fundamental_request_period_types",
            index_column="period_type_index",
            unique_column="period_type",
            mutable_columns=("period_type",),
            rows=period_types,
            cache_entry_id=cache_entry_id,
            refresh_fingerprint=stored.storage_fingerprint,
        )

        snapshot = stored.retrieval.snapshot
        if snapshot is None:
            raise StorageError(
                "stored fundamental snapshot is missing its evidence payload"
            )
        sources = [
            [
                cache_entry_id,
                index,
                source.source_id,
                _enum_value(source.source_type),
                _enum_value(source.source_rank),
                source.as_of_date,
                source.published_at,
                source.retrieved_at,
                _enum_value(source.freshness_status),
                _enum_value(source.validation_status),
                source.content_fingerprint,
                source.model_dump_json(),
            ]
            for index, source in enumerate(snapshot.sources)
        ]
        self._sync_fundamental_detail_rows(
            table="jarvis_fundamental_sources",
            index_column="source_index",
            unique_column="source_id",
            mutable_columns=(
                "source_id",
                "source_type",
                "source_rank",
                "as_of_date",
                "published_at",
                "retrieved_at",
                "freshness_status",
                "validation_status",
                "content_fingerprint",
                "source_json",
            ),
            rows=sources,
            cache_entry_id=cache_entry_id,
            refresh_fingerprint=stored.storage_fingerprint,
        )

        facts = [
            [
                cache_entry_id,
                index,
                fact.evidence_id,
                _enum_value(fact.statement),
                fact.line_item_id,
                fact.line_item_standard,
                fact.period.label,
                _enum_value(fact.period.period_type),
                fact.period.start_date,
                fact.period.end_date,
                _enum_value(fact.value_kind),
                (
                    str(fact.normalized_value)
                    if fact.normalized_value is not None
                    else None
                ),
                fact.currency,
                fact.normalized_unit,
                _enum_value(fact.availability_status),
                _enum_value(fact.evidence_label),
                _enum_value(fact.confidence),
                _enum_value(fact.freshness_status),
                _enum_value(fact.validation_status),
                _enum_value(fact.conflict_status),
                fact.model_dump_json(),
            ]
            for index, fact in enumerate(snapshot.facts)
        ]
        self._sync_fundamental_detail_rows(
            table="jarvis_fundamental_facts",
            index_column="fact_index",
            unique_column="evidence_id",
            mutable_columns=(
                "evidence_id",
                "statement",
                "line_item_id",
                "line_item_standard",
                "period_label",
                "period_type",
                "period_start",
                "period_end",
                "value_kind",
                "normalized_value",
                "currency",
                "normalized_unit",
                "availability_status",
                "evidence_label",
                "confidence",
                "freshness_status",
                "validation_status",
                "conflict_status",
                "fact_json",
            ),
            rows=facts,
            cache_entry_id=cache_entry_id,
            refresh_fingerprint=stored.storage_fingerprint,
        )

        conflicts = [
            [
                cache_entry_id,
                index,
                conflict.conflict_id,
                _enum_value(conflict.conflict_type),
                _enum_value(conflict.status),
                conflict.material,
                conflict.working_evidence_id,
                conflict.model_dump_json(),
            ]
            for index, conflict in enumerate(snapshot.conflicts)
        ]
        self._sync_fundamental_detail_rows(
            table="jarvis_fundamental_conflicts",
            index_column="conflict_index",
            unique_column="conflict_id",
            mutable_columns=(
                "conflict_id",
                "conflict_type",
                "status",
                "material",
                "working_evidence_id",
                "conflict_json",
            ),
            rows=conflicts,
            cache_entry_id=cache_entry_id,
            refresh_fingerprint=stored.storage_fingerprint,
        )

    def _sync_fundamental_detail_rows(
        self,
        *,
        table: str,
        index_column: str,
        unique_column: str,
        mutable_columns: tuple[str, ...],
        rows: list[list[object]],
        cache_entry_id: str,
        refresh_fingerprint: str,
    ) -> None:
        """Replace indexed child rows without delete/reinsert index churn."""

        existing_indices = {
            row[0]
            for row in self._connection.execute(
                f"SELECT {index_column} FROM {table} "
                "WHERE cache_entry_id = ?",
                [cache_entry_id],
            ).fetchall()
        }
        if existing_indices:
            placeholder = f"__jarvis_refresh__{refresh_fingerprint}:"
            self._connection.execute(
                f"UPDATE {table} SET {unique_column} = ? || "
                f"CAST({index_column} AS VARCHAR) "
                "WHERE cache_entry_id = ?",
                [placeholder, cache_entry_id],
            )

        assignments = ", ".join(
            f"{column} = ?" for column in mutable_columns
        )
        for row in rows:
            index = row[1]
            if index in existing_indices:
                self._connection.execute(
                    f"UPDATE {table} SET {assignments} "
                    f"WHERE cache_entry_id = ? AND {index_column} = ?",
                    [*row[2:], cache_entry_id, index],
                )
            else:
                self._connection.execute(
                    f"INSERT INTO {table} VALUES ("
                    + ", ".join("?" for _ in row)
                    + ")",
                    row,
                )

        retained_indices = {row[1] for row in rows}
        surplus_indices = existing_indices - retained_indices
        for index in surplus_indices:
            self._connection.execute(
                f"DELETE FROM {table} WHERE cache_entry_id = ? "
                f"AND {index_column} = ?",
                [cache_entry_id, index],
            )

    def _delete_fundamental_entry_locked(self, cache_entry_id: str) -> None:
        for table in (
            "jarvis_fundamental_request_statements",
            "jarvis_fundamental_request_period_types",
            "jarvis_fundamental_sources",
            "jarvis_fundamental_facts",
            "jarvis_fundamental_conflicts",
        ):
            self._connection.execute(
                f"DELETE FROM {table} WHERE cache_entry_id = ?",
                [cache_entry_id],
            )
        self._connection.execute(
            "DELETE FROM jarvis_fundamental_snapshots "
            "WHERE cache_entry_id = ?",
            [cache_entry_id],
        )

    def _purge_expired_fundamental_locked(
        self,
        *,
        as_of: datetime,
    ) -> int:
        rows = self._connection.execute(
            "SELECT cache_entry_id FROM jarvis_fundamental_snapshots "
            "WHERE expires_at <= ? ORDER BY cache_entry_id",
            [as_of],
        ).fetchall()
        for (cache_entry_id,) in rows:
            self._delete_fundamental_entry_locked(cache_entry_id)
        return len(rows)

    def _insert_structured_document(
        self,
        stored: StoredStructuredFinancialDocument,
    ) -> None:
        key = stored.cache_key
        issuer = key.issuer
        document = stored.result.document
        if document is None:
            raise StorageError("stored structured result is missing its document")
        values = [
            key.cache_entry_id,
            key.cache_key_fingerprint,
            stored.storage_fingerprint,
            key.tenant_id,
            key.provider_connection_id,
            key.provider,
            issuer.exchange,
            issuer.symbol,
            issuer.isin,
            issuer.provider_company_id,
            issuer.provider_slug,
            key.as_of_date,
            _enum_value(key.document_type),
            _enum_value(key.reporting_basis),
            stored.request.request_id,
            stored.request_fingerprint,
            stored.result_fingerprint,
            stored.document_fingerprint,
            stored.retrieved_at,
            stored.expires_at,
            stored.stored_at,
            len(document.periods),
            len(document.rows),
            stored.model_dump_json(exclude_computed_fields=True),
        ]
        self._connection.execute(
            "INSERT INTO jarvis_structured_financial_documents VALUES ("
            + ", ".join("?" for _ in values)
            + ")",
            values,
        )

    def _delete_structured_document_locked(self, cache_entry_id: str) -> None:
        self._connection.execute(
            "DELETE FROM jarvis_structured_financial_documents "
            "WHERE cache_entry_id = ?",
            [cache_entry_id],
        )

    def _purge_expired_structured_documents_locked(
        self,
        *,
        as_of: datetime,
    ) -> int:
        rows = self._connection.execute(
            "SELECT cache_entry_id FROM jarvis_structured_financial_documents "
            "WHERE expires_at <= ? ORDER BY cache_entry_id",
            [as_of],
        ).fetchall()
        for (cache_entry_id,) in rows:
            self._delete_structured_document_locked(cache_entry_id)
        return len(rows)

    @staticmethod
    def _validate_structured_document_writable(
        stored: StoredStructuredFinancialDocument,
        *,
        as_of: datetime,
    ) -> None:
        if stored.stored_at > as_of:
            raise StorageError(
                "structured document storage time is in the future"
            )
        if stored.is_expired(as_of=as_of):
            raise StorageError("expired structured document cannot be saved")

    @staticmethod
    def _copy_structured_document(
        stored: StoredStructuredFinancialDocument,
    ) -> StoredStructuredFinancialDocument:
        return StoredStructuredFinancialDocument.model_validate_json(
            stored.model_dump_json(exclude_computed_fields=True)
        )

    def _insert_peer_comparison(
        self,
        stored: StoredPeerComparisonDocument,
    ) -> None:
        key = stored.cache_key
        issuer = key.issuer
        document = stored.result.document
        if document is None:
            raise StorageError("stored Peer Comparison is missing its document")
        values = [
            key.cache_entry_id,
            key.cache_key_fingerprint,
            stored.storage_fingerprint,
            key.tenant_id,
            key.provider_connection_id,
            key.provider,
            issuer.exchange,
            issuer.symbol,
            issuer.isin,
            issuer.provider_company_id,
            issuer.provider_slug,
            key.as_of_date,
            key.document_type,
            stored.request.request_id,
            stored.request_fingerprint,
            stored.result_fingerprint,
            stored.document_fingerprint,
            document.observation_date,
            stored.retrieved_at,
            stored.expires_at,
            stored.stored_at,
            len(document.metrics),
            len(document.peers),
            stored.model_dump_json(exclude_computed_fields=True),
        ]
        self._connection.execute(
            "INSERT INTO jarvis_peer_comparisons VALUES ("
            + ", ".join("?" for _ in values)
            + ")",
            values,
        )

    def _delete_peer_comparison_locked(self, cache_entry_id: str) -> None:
        self._connection.execute(
            "DELETE FROM jarvis_peer_comparisons WHERE cache_entry_id = ?",
            [cache_entry_id],
        )

    def _purge_expired_peer_comparisons_locked(
        self,
        *,
        as_of: datetime,
    ) -> int:
        rows = self._connection.execute(
            "SELECT cache_entry_id FROM jarvis_peer_comparisons "
            "WHERE expires_at <= ? ORDER BY cache_entry_id",
            [as_of],
        ).fetchall()
        for (cache_entry_id,) in rows:
            self._delete_peer_comparison_locked(cache_entry_id)
        return len(rows)

    @staticmethod
    def _validate_peer_comparison_writable(
        stored: StoredPeerComparisonDocument,
        *,
        as_of: datetime,
    ) -> None:
        if stored.stored_at > as_of:
            raise StorageError("Peer Comparison storage time is in the future")
        if stored.is_expired(as_of=as_of):
            raise StorageError("expired Peer Comparison cannot be saved")

    @staticmethod
    def _copy_peer_comparison(
        stored: StoredPeerComparisonDocument,
    ) -> StoredPeerComparisonDocument:
        return StoredPeerComparisonDocument.model_validate_json(
            stored.model_dump_json(exclude_computed_fields=True)
        )

    def _insert_benchmarking_financials(
        self,
        stored: StoredBenchmarkingFinancialsDocument,
    ) -> None:
        key = stored.cache_key
        issuer = key.issuer
        document = stored.result.document
        if document is None:
            raise StorageError(
                "stored Benchmarking Financials is missing its document"
            )
        values = [
            key.cache_entry_id,
            key.cache_key_fingerprint,
            stored.storage_fingerprint,
            key.tenant_id,
            key.provider_connection_id,
            key.provider,
            issuer.exchange,
            issuer.symbol,
            issuer.isin,
            issuer.provider_company_id,
            issuer.provider_slug,
            key.as_of_date,
            key.document_type,
            stored.request.request_id,
            stored.request_fingerprint,
            stored.result_fingerprint,
            stored.document_fingerprint,
            document.observation_date,
            stored.retrieved_at,
            stored.expires_at,
            stored.stored_at,
            len(document.companies),
            len(document.rows),
            stored.model_dump_json(exclude_computed_fields=True),
        ]
        self._connection.execute(
            "INSERT INTO jarvis_benchmarking_financials VALUES ("
            + ", ".join("?" for _ in values)
            + ")",
            values,
        )

    def _delete_benchmarking_financials_locked(
        self,
        cache_entry_id: str,
    ) -> None:
        self._connection.execute(
            "DELETE FROM jarvis_benchmarking_financials "
            "WHERE cache_entry_id = ?",
            [cache_entry_id],
        )

    def _purge_expired_benchmarking_financials_locked(
        self,
        *,
        as_of: datetime,
    ) -> int:
        rows = self._connection.execute(
            "SELECT cache_entry_id FROM jarvis_benchmarking_financials "
            "WHERE expires_at <= ? ORDER BY cache_entry_id",
            [as_of],
        ).fetchall()
        for (cache_entry_id,) in rows:
            self._delete_benchmarking_financials_locked(cache_entry_id)
        return len(rows)

    @staticmethod
    def _validate_benchmarking_financials_writable(
        stored: StoredBenchmarkingFinancialsDocument,
        *,
        as_of: datetime,
    ) -> None:
        if stored.stored_at > as_of:
            raise StorageError(
                "Benchmarking Financials storage time is in the future"
            )
        if stored.is_expired(as_of=as_of):
            raise StorageError(
                "expired Benchmarking Financials cannot be saved"
            )

    @staticmethod
    def _copy_benchmarking_financials(
        stored: StoredBenchmarkingFinancialsDocument,
    ) -> StoredBenchmarkingFinancialsDocument:
        return StoredBenchmarkingFinancialsDocument.model_validate_json(
            stored.model_dump_json(exclude_computed_fields=True)
        )

    @staticmethod
    def _fundamental_package_scope_fingerprint(
        scope: FundamentalPackageRepositoryScope,
    ) -> str:
        return sha256(scope.model_dump_json().encode("utf-8")).hexdigest()

    def _insert_fundamental_package(
        self,
        package: FundamentalEvidencePackage,
        scope_fingerprint: str,
    ) -> None:
        manifest = package.manifest
        self._connection.execute(
            "INSERT INTO jarvis_fundamental_packages VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                package.package_id,
                package.aggregate_fingerprint,
                manifest.manifest_fingerprint,
                scope_fingerprint,
                manifest.connection.tenant_id,
                manifest.connection.provider_connection_id,
                manifest.connection.provider,
                manifest.issuer.exchange,
                manifest.issuer.symbol,
                manifest.operation_id,
                manifest.committed_at,
                manifest.expires_at,
                package.model_dump_json(exclude_computed_fields=True),
            ],
        )

    def _purge_expired_fundamental_packages_locked(
        self,
        *,
        as_of: datetime,
    ) -> int:
        rows = self._connection.execute(
            "SELECT package_id FROM jarvis_fundamental_packages "
            "WHERE expires_at <= ? ORDER BY package_id",
            [as_of],
        ).fetchall()
        package_ids = tuple(row[0] for row in rows)
        for package_id in package_ids:
            self._connection.execute(
                "DELETE FROM jarvis_active_fundamental_packages "
                "WHERE package_id = ?",
                [package_id],
            )
            self._connection.execute(
                "DELETE FROM jarvis_fundamental_packages WHERE package_id = ?",
                [package_id],
            )
        return len(package_ids)

    @staticmethod
    def _validate_fundamental_package_writable(
        package: FundamentalEvidencePackage,
        *,
        as_of: datetime,
    ) -> None:
        if package.manifest.committed_at > as_of:
            raise StorageError(
                "fundamental package commit time is in the future"
            )
        if package.is_expired(as_of=as_of):
            raise StorageError("expired fundamental package cannot be committed")

    @staticmethod
    def _copy_fundamental_package(
        package: FundamentalEvidencePackage,
    ) -> FundamentalEvidencePackage:
        return FundamentalEvidencePackage.model_validate_json(
            package.model_dump_json(exclude_computed_fields=True)
        )

    @staticmethod
    def _copy_corporate_filings_analysis(
        record: StoredCorporateFilingsAnalysis,
    ) -> StoredCorporateFilingsAnalysis:
        return StoredCorporateFilingsAnalysis.from_storage_json(
            record.to_storage_json()
        )

    @staticmethod
    def _copy_corporate_analysis_delete_receipt(
        receipt: CorporateFilingsAnalysisDeleteReceipt,
    ) -> CorporateFilingsAnalysisDeleteReceipt:
        return CorporateFilingsAnalysisDeleteReceipt.model_validate_json(
            receipt.model_dump_json(exclude_computed_fields=True)
        )

    @staticmethod
    def _decode_scoped_corporate_filings_analysis(
        payload_json: str,
        *,
        scope: CorporateFilingsAnalysisRepositoryScope,
    ) -> StoredCorporateFilingsAnalysis:
        value = StoredCorporateFilingsAnalysis.from_storage_json(payload_json)
        if value.scope != scope:
            raise StorageError(
                "DuckDB corporate analysis scope metadata is inconsistent"
            )
        return value

    @staticmethod
    def _validate_corporate_filings_analysis_writable(
        record: StoredCorporateFilingsAnalysis,
        *,
        as_of: datetime,
    ) -> None:
        if record.stored_at > as_of:
            raise StorageError(
                "corporate filings analysis storage time is in the future"
            )

    @staticmethod
    def _validate_corporate_analysis_record_id(record_id: str) -> None:
        if not isinstance(record_id, str) or fullmatch(
            _CORPORATE_FILINGS_ANALYSIS_RECORD_ID_PATTERN,
            record_id,
        ) is None:
            raise ValueError("corporate filings analysis record ID is invalid")

    @classmethod
    def _validate_optional_corporate_analysis_record_id(
        cls,
        record_id: str | None,
    ) -> None:
        if record_id is not None:
            cls._validate_corporate_analysis_record_id(record_id)

    def _validate_corporate_analysis_conflicts_locked(
        self,
        value: StoredCorporateFilingsAnalysis,
    ) -> StoredCorporateFilingsAnalysis | None:
        existing_row = self._connection.execute(
            "SELECT payload_json FROM jarvis_corporate_filings_analysis "
            "WHERE record_id = ?",
            [value.record_id],
        ).fetchone()
        existing = None
        if existing_row is not None:
            existing = StoredCorporateFilingsAnalysis.from_storage_json(
                existing_row[0]
            )
            if existing != value:
                raise StorageConflictError(
                    "corporate analysis record ID contains different data"
                )

        reuse_rows = self._connection.execute(
            "SELECT record_id FROM jarvis_corporate_filings_analysis "
            "WHERE scope_fingerprint = ? AND reuse_key_fingerprint = ?",
            [
                value.scope.scope_fingerprint,
                value.reuse_key.reuse_key_fingerprint,
            ],
        ).fetchall()
        if any(row[0] != value.record_id for row in reuse_rows):
            raise StorageConflictError(
                "corporate analysis reuse key contains different data"
            )
        return existing

    def _insert_corporate_filings_analysis_locked(
        self,
        value: StoredCorporateFilingsAnalysis,
    ) -> None:
        execution = value.result.execution
        if execution is None:
            raise StorageError(
                "corporate filings analysis execution metadata is missing"
            )
        columns = (
            "record_id",
            "record_fingerprint",
            "scope_fingerprint",
            "tenant_id",
            "provider_connection_id",
            "provider",
            "exchange",
            "symbol",
            "isin",
            "fiscal_year",
            "fiscal_quarter",
            "analysis_kind",
            "period_key_fingerprint",
            "reuse_key_fingerprint",
            "request_fingerprint",
            "filing_package_id",
            "filing_package_fingerprint",
            "bundle_fingerprint",
            "policy_fingerprint",
            "skill_id",
            "skill_version",
            "skill_sha256",
            "model_configuration_fingerprint",
            "result_fingerprint",
            "analysis_completed_at",
            "stored_at",
            "payload_json",
        )
        period = value.period
        values = [
            value.record_id,
            value.record_fingerprint,
            value.scope.scope_fingerprint,
            value.scope.connection.tenant_id,
            value.scope.connection.provider_connection_id,
            value.scope.connection.provider,
            value.scope.issuer.exchange,
            value.scope.issuer.symbol,
            value.scope.issuer.isin,
            period.fiscal_year,
            period.fiscal_quarter,
            value.analysis_kind.value,
            value.period_key.period_key_fingerprint,
            value.reuse_key.reuse_key_fingerprint,
            value.request_fingerprint,
            value.filing_package_id,
            value.filing_package_fingerprint,
            value.bundle_fingerprint,
            value.policy_fingerprint,
            value.skill_id,
            value.skill_version,
            value.skill_sha256,
            value.model_configuration_fingerprint,
            value.result.result_fingerprint,
            execution.completed_at,
            value.stored_at,
            value.to_storage_json(),
        ]
        self._connection.execute(
            "INSERT INTO jarvis_corporate_filings_analysis ("
            + ", ".join(columns)
            + ") VALUES ("
            + ", ".join("?" for _ in values)
            + ")",
            values,
        )

    def _read_corporate_analysis_delete_receipt_locked(
        self,
        authorization: CorporateFilingsAnalysisDeleteAuthorization,
    ) -> CorporateFilingsAnalysisDeleteReceipt | None:
        row = self._connection.execute(
            "SELECT payload_json "
            "FROM jarvis_corporate_filings_analysis_delete_receipts "
            "WHERE audit_id = ?",
            [authorization.audit_id],
        ).fetchone()
        if row is None:
            return None
        receipt = CorporateFilingsAnalysisDeleteReceipt.model_validate_json(
            row[0]
        )
        if receipt.authorization != authorization:
            raise StorageConflictError(
                "corporate analysis deletion audit ID was reused"
            )
        return self._copy_corporate_analysis_delete_receipt(receipt)

    def _insert_corporate_analysis_delete_receipt_locked(
        self,
        receipt: CorporateFilingsAnalysisDeleteReceipt,
    ) -> None:
        authorization = receipt.authorization
        self._connection.execute(
            "INSERT INTO jarvis_corporate_filings_analysis_delete_receipts "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                authorization.audit_id,
                authorization.operation_id,
                authorization.authorized_by,
                authorization.record_id,
                authorization.record_fingerprint,
                receipt.status.value,
                authorization.authorized_at,
                receipt.completed_at,
                receipt.model_dump_json(exclude_computed_fields=True),
            ],
        )

    @staticmethod
    def _filing_scope_fingerprint(
        scope: FundamentalFilingRepositoryScope,
    ) -> str:
        return sha256(scope.model_dump_json().encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_filing_identifier(
        value: str,
        *,
        pattern: str,
        label: str,
    ) -> None:
        if not isinstance(value, str) or fullmatch(pattern, value) is None:
            raise ValueError(f"{label} is invalid")

    @classmethod
    def _validate_optional_filing_package_id(cls, value: str | None) -> None:
        if value is not None:
            cls._validate_filing_identifier(
                value,
                pattern=_FILING_PACKAGE_ID_PATTERN,
                label="expected active filing package ID",
            )

    @staticmethod
    def _validate_filing_artifact_writable(
        artifact: FundamentalFilingArtifactManifest,
        *,
        as_of: datetime,
    ) -> None:
        if artifact.last_checked_at > as_of:
            raise StorageError("filing artifact check time is in the future")

    @staticmethod
    def _validate_filing_package_writable(
        package: FundamentalFilingPackage,
        *,
        as_of: datetime,
    ) -> None:
        if package.manifest.committed_at > as_of:
            raise StorageError("filing package commit time is in the future")

    @staticmethod
    def _copy_filing_artifact(
        artifact: FundamentalFilingArtifactManifest,
    ) -> FundamentalFilingArtifactManifest:
        return FundamentalFilingArtifactManifest.model_validate_json(
            artifact.model_dump_json(exclude_computed_fields=True)
        )

    @staticmethod
    def _copy_filing_package(
        package: FundamentalFilingPackage,
    ) -> FundamentalFilingPackage:
        return FundamentalFilingPackage.model_validate_json(
            package.model_dump_json(exclude_computed_fields=True)
        )

    @staticmethod
    def _copy_filing_delete_receipt(
        receipt: FundamentalFilingDeleteReceipt,
    ) -> FundamentalFilingDeleteReceipt:
        return FundamentalFilingDeleteReceipt.model_validate_json(
            receipt.model_dump_json(exclude_computed_fields=True)
        )

    @staticmethod
    def _copy_filing_evidence(
        record: StoredFundamentalFilingEvidence,
    ) -> StoredFundamentalFilingEvidence:
        return StoredFundamentalFilingEvidence.from_storage_json(
            record.to_storage_json()
        )

    @staticmethod
    def _decode_scoped_filing_evidence(
        payload_json: str,
        *,
        scope: FundamentalFilingRepositoryScope,
    ) -> StoredFundamentalFilingEvidence:
        value = StoredFundamentalFilingEvidence.from_storage_json(payload_json)
        if value.scope != scope:
            raise StorageError(
                "DuckDB filing evidence scope metadata is inconsistent"
            )
        return value

    def _validate_filing_artifact_conflict_locked(
        self,
        artifact: FundamentalFilingArtifactManifest,
    ) -> FundamentalFilingArtifactManifest | None:
        row = self._connection.execute(
            "SELECT payload_json "
            "FROM jarvis_fundamental_filing_artifacts "
            "WHERE artifact_id = ?",
            [artifact.artifact_id],
        ).fetchone()
        if row is None:
            return None
        existing = FundamentalFilingArtifactManifest.model_validate_json(
            row[0]
        )
        if existing != artifact:
            raise StorageConflictError(
                "filing artifact ID contains different metadata"
            )
        return existing

    def _save_filing_artifact_locked(
        self,
        artifact: FundamentalFilingArtifactManifest,
    ) -> FundamentalFilingArtifactManifest:
        existing = self._validate_filing_artifact_conflict_locked(artifact)
        if existing is not None:
            return self._copy_filing_artifact(existing)

        scope = FundamentalFilingRepositoryScope.from_artifact(artifact)
        reference = artifact.reference
        self._connection.execute(
            "INSERT INTO jarvis_fundamental_filing_artifacts VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                artifact.artifact_id,
                artifact.artifact_fingerprint,
                artifact.content_sha256,
                self._filing_scope_fingerprint(scope),
                reference.connection.tenant_id,
                reference.connection.provider_connection_id,
                reference.connection.provider,
                reference.issuer.exchange,
                reference.issuer.symbol,
                reference.issuer.isin,
                reference.filing_reference_id,
                reference.category.value,
                artifact.safe_relative_path,
                artifact.downloaded_at,
                artifact.last_checked_at,
                artifact.recheck_after,
                artifact.model_dump_json(exclude_computed_fields=True),
            ],
        )
        return self._copy_filing_artifact(artifact)

    def _validate_filing_artifact_links_locked(
        self,
        artifact: FundamentalFilingArtifactManifest,
        *,
        pending: dict[str, FundamentalFilingArtifactManifest],
    ) -> None:
        artifact_scope = FundamentalFilingRepositoryScope.from_artifact(
            artifact
        )
        for relation, related_id in (
            ("duplicate", artifact.duplicate_of_artifact_id),
            ("supersedes", artifact.supersedes_artifact_id),
        ):
            if related_id is None:
                continue
            related = pending.get(related_id)
            if related is None:
                row = self._connection.execute(
                    "SELECT payload_json "
                    "FROM jarvis_fundamental_filing_artifacts "
                    "WHERE artifact_id = ?",
                    [related_id],
                ).fetchone()
                if row is not None:
                    related = (
                        FundamentalFilingArtifactManifest.model_validate_json(
                            row[0]
                        )
                    )
            if related is None or (
                FundamentalFilingRepositoryScope.from_artifact(related)
                != artifact_scope
            ):
                raise StorageConflictError(
                    f"filing artifact {relation} link is unavailable in scope"
                )
            if relation == "duplicate":
                if related.content_sha256 != artifact.content_sha256:
                    raise StorageConflictError(
                        "duplicate filing artifacts must share a content hash"
                    )
            elif (
                related.reference.filing_reference_id
                != artifact.reference.filing_reference_id
                or related.content_sha256 == artifact.content_sha256
            ):
                raise StorageConflictError(
                    "superseded filing artifact lineage is inconsistent"
                )

    def _insert_filing_package_locked(
        self,
        package: FundamentalFilingPackage,
        *,
        scope_fingerprint: str,
    ) -> None:
        manifest = package.manifest
        self._connection.execute(
            "INSERT INTO jarvis_fundamental_filing_packages VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                package.package_id,
                manifest.manifest_fingerprint,
                manifest.discovery_fingerprint,
                scope_fingerprint,
                manifest.connection.tenant_id,
                manifest.connection.provider_connection_id,
                manifest.connection.provider,
                manifest.issuer.exchange,
                manifest.issuer.symbol,
                manifest.issuer.isin,
                manifest.operation_id,
                manifest.committed_at,
                manifest.recheck_after,
                package.model_dump_json(exclude_computed_fields=True),
            ],
        )
        if package.artifacts:
            self._connection.executemany(
                "INSERT INTO jarvis_fundamental_filing_package_artifacts "
                "VALUES (?, ?)",
                [
                    [package.package_id, artifact.artifact_id]
                    for artifact in package.artifacts
                ],
            )

    @staticmethod
    def _validate_filing_delete_authorization(
        authorization: FundamentalFilingDeleteAuthorization,
        *,
        target_kind: FundamentalFilingDeleteTarget,
        target_id: str,
    ) -> FundamentalFilingDeleteAuthorization:
        value = FundamentalFilingDeleteAuthorization.model_validate(
            authorization
        )
        if value.target_kind is not target_kind or value.target_id != target_id:
            raise StorageError(
                "filing deletion authorization does not match the target"
            )
        return value

    @staticmethod
    def _require_filing_authorization_not_future(
        authorization: FundamentalFilingDeleteAuthorization,
        *,
        as_of: datetime,
    ) -> None:
        if authorization.authorized_at > as_of:
            raise StorageError(
                "filing deletion authorization is in the future"
            )

    def _read_filing_delete_receipt_locked(
        self,
        authorization: FundamentalFilingDeleteAuthorization,
    ) -> FundamentalFilingDeleteReceipt | None:
        row = self._connection.execute(
            "SELECT payload_json "
            "FROM jarvis_fundamental_filing_delete_receipts "
            "WHERE audit_id = ?",
            [authorization.audit_id],
        ).fetchone()
        if row is None:
            return None
        receipt = FundamentalFilingDeleteReceipt.model_validate_json(row[0])
        if receipt.authorization != authorization:
            raise StorageConflictError(
                "filing deletion audit ID contains different authorization"
            )
        return self._copy_filing_delete_receipt(receipt)

    def _insert_filing_delete_receipt_locked(
        self,
        receipt: FundamentalFilingDeleteReceipt,
    ) -> None:
        authorization = receipt.authorization
        self._connection.execute(
            "INSERT INTO jarvis_fundamental_filing_delete_receipts VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                authorization.audit_id,
                authorization.operation_id,
                authorization.authorized_by,
                authorization.target_kind.value,
                authorization.target_id,
                receipt.status.value,
                authorization.authorized_at,
                receipt.completed_at,
                receipt.model_dump_json(exclude_computed_fields=True),
            ],
        )

    def _get_payload(
        self,
        *,
        table: str,
        id_column: str,
        identifier: str,
        payload_type,
    ):
        with self._lock:
            self._ensure_open()
            try:
                row = self._connection.execute(
                    f"SELECT payload_json FROM {table} "
                    f"WHERE {id_column} = ?",
                    [identifier],
                ).fetchone()
                if row is None:
                    return None
                return payload_type.model_validate_json(row[0])
            except (duckdb.Error, ValidationError, ValueError) as exc:
                raise StorageError(
                    "Unable to load DuckDB research data"
                ) from exc

    def _list_summaries(
        self,
        *,
        table: str,
        id_column: str,
        clauses: list[str],
        parameters: list[object],
        limit: int,
        offset: int,
        summary_type,
    ):
        where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            f"SELECT summary_json FROM {table}{where_sql} "
            f"ORDER BY stored_at DESC, {id_column} DESC LIMIT ? OFFSET ?"
        )
        with self._lock:
            self._ensure_open()
            try:
                rows = self._connection.execute(
                    sql,
                    [*parameters, limit, offset],
                ).fetchall()
                return tuple(
                    summary_type.model_validate_json(row[0])
                    for row in rows
                )
            except (duckdb.Error, ValidationError, ValueError) as exc:
                raise StorageError(
                    "Unable to list DuckDB research data"
                ) from exc

    def _delete(
        self,
        *,
        table: str,
        id_column: str,
        identifier: str,
        child_relations: tuple[tuple[str, str], ...] = (),
    ) -> bool:
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                exists = self._connection.execute(
                    f"SELECT 1 FROM {table} WHERE {id_column} = ?",
                    [identifier],
                ).fetchone()
                if exists is not None:
                    for child_table, child_id_column in child_relations:
                        self._connection.execute(
                            f"DELETE FROM {child_table} "
                            f"WHERE {child_id_column} = ?",
                            [identifier],
                        )
                    self._connection.execute(
                        f"DELETE FROM {table} WHERE {id_column} = ?",
                        [identifier],
                    )
                self._connection.execute("COMMIT")
                return exists is not None
            except duckdb.Error as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to delete DuckDB research data"
                ) from exc

    def _rollback_safely(self) -> None:
        try:
            self._connection.execute("ROLLBACK")
        except duckdb.Error:
            pass

    def _ensure_open(self) -> None:
        if self._closed:
            raise StorageError("DuckDB research storage is closed")

    def _close_after_initialization_failure(self) -> None:
        connection = getattr(self, "_connection", None)
        if connection is not None:
            try:
                connection.close()
            except duckdb.Error:
                pass
        self._closed = True


def _common_filter_clauses(query) -> tuple[list[str], list[object]]:
    clauses = []
    parameters = []
    if query.stored_from is not None:
        clauses.append("stored_at >= ?")
        parameters.append(query.stored_from)
    if query.stored_to is not None:
        clauses.append("stored_at <= ?")
        parameters.append(query.stored_to)
    return clauses, parameters


def _require_aware_datetime(
    value: datetime,
    field_name: str,
) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include timezone information")
    return value


def _enum_value(value):
    return value.value if value is not None else None


def _append_filter(
    clauses: list[str],
    parameters: list[object],
    column: str,
    value: str | None,
) -> None:
    if value is not None:
        clauses.append(f"{column} = ?")
        parameters.append(value)
