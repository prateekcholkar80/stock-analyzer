from pathlib import Path
from threading import RLock
from types import TracebackType

import duckdb
from pydantic import ValidationError

from app.exceptions import StorageConflictError, StorageError
from app.models.storage import (
    BacktestRunQuery,
    BacktestRunSummary,
    MarketSeriesQuery,
    MarketSeriesSummary,
    StoredBacktestRun,
    StoredMarketSeries,
    backtest_run_summary,
    market_series_summary,
)


_SCHEMA_VERSION = 1


class DuckDBJarvisStorage:
    """Persistent DuckDB implementation of the Jarvis storage ports."""

    adapter_name = "duckdb"

    def __init__(self, database: str | Path) -> None:
        database_name = str(database)
        if not database_name.strip():
            raise ValueError("DuckDB database path cannot be blank")
        self._database = database_name
        self._lock = RLock()
        self._closed = False
        try:
            self._connection = duckdb.connect(database_name)
            self._initialize_schema()
        except StorageError:
            self._close_after_initialization_failure()
            raise
        except duckdb.Error as exc:
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
        parameters = [
            value.dataset_id,
            value.payload_fingerprint,
            value.stored_at,
            value.series.exchange,
            value.series.symbol_token,
            value.series.interval,
            value.series.source,
            summary.model_dump_json(),
            value.model_dump_json(),
        ]
        return self._save_immutable(
            table="jarvis_market_series",
            id_column="dataset_id",
            identifier=value.dataset_id,
            fingerprint_column="payload_fingerprint",
            fingerprint=value.payload_fingerprint,
            payload_type=StoredMarketSeries,
            insert_sql=(
                "INSERT INTO jarvis_market_series ("
                "dataset_id, payload_fingerprint, stored_at, exchange, "
                "symbol_token, interval, source, summary_json, payload_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            ),
            insert_parameters=parameters,
            conflict_message=(
                "market dataset identifier already contains different data"
            ),
        )

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
        )

    def save_backtest_run(
        self,
        stored: StoredBacktestRun,
    ) -> StoredBacktestRun:
        value = StoredBacktestRun.model_validate(stored)
        summary = backtest_run_summary(value)
        result = value.result
        series = result.market_series
        parameters = [
            value.run_id,
            value.result_fingerprint,
            value.market_fingerprint,
            value.stored_at,
            result.backtest_id,
            result.engine_id,
            series.exchange,
            series.symbol_token,
            series.interval,
            summary.model_dump_json(),
            value.model_dump_json(),
        ]
        return self._save_immutable(
            table="jarvis_backtest_runs",
            id_column="run_id",
            identifier=value.run_id,
            fingerprint_column="result_fingerprint",
            fingerprint=value.result_fingerprint,
            payload_type=StoredBacktestRun,
            insert_sql=(
                "INSERT INTO jarvis_backtest_runs ("
                "run_id, result_fingerprint, market_fingerprint, "
                "stored_at, backtest_id, engine_id, exchange, "
                "symbol_token, interval, summary_json, payload_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            ),
            insert_parameters=parameters,
            conflict_message=(
                "backtest run identifier already contains different data"
            ),
        )

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
        )

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
                if row is None:
                    self._connection.execute(
                        "INSERT INTO jarvis_storage_metadata VALUES (?, ?)",
                        ["schema_version", str(_SCHEMA_VERSION)],
                    )
                elif row[0] != str(_SCHEMA_VERSION):
                    raise StorageError(
                        "Unsupported DuckDB storage schema version: "
                        f"{row[0]}"
                    )
                self._create_tables()
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

    def _save_immutable(
        self,
        *,
        table: str,
        id_column: str,
        identifier: str,
        fingerprint_column: str,
        fingerprint: str,
        payload_type,
        insert_sql: str,
        insert_parameters: list[object],
        conflict_message: str,
    ):
        with self._lock:
            self._ensure_open()
            transaction_started = False
            try:
                self._connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                existing = self._connection.execute(
                    f"SELECT {fingerprint_column}, payload_json "
                    f"FROM {table} WHERE {id_column} = ?",
                    [identifier],
                ).fetchone()
                if existing is not None:
                    if existing[0] != fingerprint:
                        raise StorageConflictError(conflict_message)
                    loaded = payload_type.model_validate_json(existing[1])
                    self._connection.execute("COMMIT")
                    return loaded
                self._connection.execute(insert_sql, insert_parameters)
                self._connection.execute("COMMIT")
                return payload_type.model_validate_json(
                    insert_parameters[-1]
                )
            except StorageConflictError:
                if transaction_started:
                    self._rollback_safely()
                raise
            except (duckdb.Error, ValidationError, ValueError) as exc:
                if transaction_started:
                    self._rollback_safely()
                raise StorageError(
                    "Unable to persist immutable DuckDB research data"
                ) from exc

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


def _append_filter(
    clauses: list[str],
    parameters: list[object],
    column: str,
    value: str | None,
) -> None:
    if value is not None:
        clauses.append(f"{column} = ?")
        parameters.append(value)
