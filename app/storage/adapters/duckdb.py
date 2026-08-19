import json
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
    MarketSeriesQuery,
    MarketSeriesSummary,
    StoredBacktestRun,
    StoredMarketSeries,
    backtest_run_summary,
    market_series_summary,
)


_SCHEMA_VERSION = 2


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
                if current_version not in (None, 1, _SCHEMA_VERSION):
                    raise StorageError(
                        "Unsupported DuckDB storage schema version: "
                        f"{row[0]}"
                    )
                self._create_tables()
                self._create_normalized_tables()
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
