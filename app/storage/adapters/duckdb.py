import json
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
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


_SCHEMA_VERSION = 4
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
                if current_version not in (None, 1, 2, 3, _SCHEMA_VERSION):
                    raise StorageError(
                        "Unsupported DuckDB storage schema version: "
                        f"{row[0]}"
                    )
                self._create_tables()
                self._create_normalized_tables()
                self._create_debate_tables()
                self._create_fundamental_tables()
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
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS jarvis_fundamental_source_lookup
            ON jarvis_fundamental_sources (
                source_type,
                source_rank,
                as_of_date
            )
            """
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
        key = stored.cache_key
        issuer = key.issuer
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
        self._connection.execute(
            "INSERT INTO jarvis_fundamental_snapshots ("
            "cache_entry_id, cache_key_fingerprint, storage_fingerprint, "
            "tenant_id, provider_connection_id, provider, capability, "
            "exchange, symbol, isin, provider_company_id, provider_slug, "
            "as_of_date, request_max_periods, request_quarters, "
            "include_promoter_pledge, "
            "request_id, request_fingerprint, result_fingerprint, "
            "snapshot_id, snapshot_fingerprint, retrieval_status, "
            "evidence_posture, validation_status, retrieved_at, expires_at, "
            "stored_at, source_count, fact_count, conflict_count, "
            "summary_json, payload_json"
            ") VALUES (" + ", ".join("?" for _ in values) + ")",
            values,
        )

    def _persist_fundamental_details(
        self,
        stored: StoredFundamentalSnapshot,
    ) -> None:
        cache_entry_id = stored.cache_key.cache_entry_id
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

        statements = [
            [cache_entry_id, index, _enum_value(statement)]
            for index, statement in enumerate(stored.cache_key.statements)
        ]
        if statements:
            self._connection.executemany(
                "INSERT INTO jarvis_fundamental_request_statements "
                "VALUES (?, ?, ?)",
                statements,
            )

        period_types = [
            [cache_entry_id, index, _enum_value(period_type)]
            for index, period_type in enumerate(
                stored.cache_key.period_types
            )
        ]
        if period_types:
            self._connection.executemany(
                "INSERT INTO jarvis_fundamental_request_period_types "
                "VALUES (?, ?, ?)",
                period_types,
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
        self._connection.executemany(
            "INSERT INTO jarvis_fundamental_sources VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            sources,
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
        self._connection.executemany(
            "INSERT INTO jarvis_fundamental_facts VALUES ("
            "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?"
            ")",
            facts,
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
        if conflicts:
            self._connection.executemany(
                "INSERT INTO jarvis_fundamental_conflicts VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?)",
                conflicts,
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
