import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

from app.analytics.walk_forward import (
    WalkForwardBacktestEngine,
    run_walk_forward_backtest,
)
from app.exceptions import StorageConflictError, StorageError
from app.gateways.fundamentals import (
    FundamentalBenchmarkingFinancialsResult,
    FundamentalCompanyOverviewRequest,
    FundamentalEvidenceRetrieval,
    FundamentalPeerComparisonResult,
    FundamentalRetrievalStatus,
    FundamentalShareholdingRequest,
)
from app.models.benchmarking_financials_storage import (
    stored_benchmarking_financials_document,
)
from app.models.backtest import WalkForwardBacktestConfig
from app.models.execution import (
    ExecutionSimulationConfig,
    ExecutionTargetPolicy,
)
from app.models.market import Candle, HistoricalCandleSeries
from app.models.financial_document_storage import (
    stored_structured_financial_document,
)
from app.models.financial_documents import (
    FinancialDocumentType,
    FinancialReportingBasis,
)
from app.models.fundamental_package import (
    FUNDAMENTAL_PACKAGE_REQUIRED_ITEMS,
    FundamentalEvidencePackage,
    FundamentalPackageItem,
    FundamentalPackageManifest,
)
from app.models.fundamental_storage import stored_fundamental_snapshot
from app.models.fundamentals import FundamentalStatement
from app.models.peer_comparison_storage import stored_peer_comparison_document
from app.models.signals import SignalDirection
from app.models.storage import (
    BacktestRunQuery,
    DebateRunQuery,
    MarketSeriesQuery,
    MultiTimeframeEndToEndSwingAnalysisResult,
    backtest_run_summary,
    market_series_summary,
    stored_backtest_run,
    stored_debate_run,
    stored_market_series,
)
from app.services.research_archive import ResearchArchiveService
from app.storage.adapters.duckdb import DuckDBJarvisStorage
from app.storage.corporate_filings_analysis_repositories import (
    CorporateFilingsAnalysisDeleteStatus,
    CorporateFilingsAnalysisRepository,
)
from app.storage.fundamental_package_repositories import (
    FundamentalPackageRepository,
    FundamentalPackageRepositoryScope,
)
from app.storage.fundamental_filing_repositories import (
    FundamentalFilingDeleteStatus,
    FundamentalFilingRepository,
    FundamentalFilingRepositoryScope,
)
from app.storage.fundamental_filing_evidence_repositories import (
    FundamentalFilingEvidencePolicyKey,
    FundamentalFilingEvidenceRepository,
)
from app.storage.repositories import JarvisStorageAdapter
from app.use_cases.run_and_archive_backtest import (
    RunAndArchiveWalkForwardBacktest,
)
from tests.unit._debate_fixtures import build_market_series
from tests.unit.test_debate_storage import _build_debate_result
from tests.unit.test_storage_adapters import (
    _backtest_result,
    _market_series,
)
from tests.unit.test_run_end_to_end_multi_timeframe_swing_analysis import (
    _use_case as _multi_timeframe_use_case,
)
from tests.unit.test_walk_forward import _ScriptedOrchestrator
from tests.unit.test_benchmarking_financials_gateway import (
    document as benchmarking_document,
    result_values as benchmarking_result_values,
)
from tests.unit.test_financial_document_storage import (
    request as financial_document_request,
    result as financial_document_result,
)
from tests.unit.test_fundamental_gateway import (
    build_connection,
    build_issuer,
    build_snapshot,
    response_values as fundamental_response_values,
)
from tests.unit.test_fundamental_storage_contracts import (
    build_financial_request,
)
from tests.unit.test_peer_comparison_gateway import (
    document as peer_document,
    result_values as peer_result_values,
)
from tests.unit.test_fundamental_filing_in_memory import (
    authorization_for,
    revised_package as revised_filing_package,
)
from tests.unit.test_fundamental_filing_evidence_in_memory import (
    revised_record as revised_filing_evidence_record,
)
from tests.unit.test_fundamental_filing_evidence_repositories import (
    stored_evidence as stored_filing_evidence,
)
from tests.unit.test_fundamental_filings import (
    PDF_HASH,
    RECHECK_AFTER,
    package as filing_package,
)
from tests.unit.test_corporate_filings_analysis_in_memory import (
    delete_authorization as corporate_analysis_delete_authorization,
    record as corporate_analysis_record,
)


IST = ZoneInfo("Asia/Kolkata")


def _fundamental_package(
    *,
    sequence: int = 1,
    connection=None,
) -> FundamentalEvidencePackage:
    requested_at = datetime(2026, 8, 30, 18, sequence, tzinfo=IST)
    completed_at = requested_at + timedelta(seconds=2)
    stored_at = requested_at + timedelta(seconds=3)
    operation_id = f"operation.coforge.package.{sequence}"
    selected_connection = connection or build_connection()
    issuer = build_issuer(
        symbol="COFORGE",
        legal_name="Coforge Ltd.",
        provider_company_id="4502",
        provider_slug="niit-technologies-limited",
    )

    overview_request = FundamentalCompanyOverviewRequest(
        request_id=f"request.coforge.overview.{sequence}",
        operation_id=operation_id,
        connection=selected_connection,
        requested_at=requested_at,
        issuer=issuer,
        as_of_date=requested_at.date(),
    )
    financials_request = build_financial_request(
        request_id=f"request.coforge.financials.{sequence}",
        operation_id=operation_id,
        connection=selected_connection,
        requested_at=requested_at,
        issuer=issuer,
        as_of_date=requested_at.date(),
    )
    shareholding_request = FundamentalShareholdingRequest(
        request_id=f"request.coforge.shareholding.{sequence}",
        operation_id=operation_id,
        connection=selected_connection,
        requested_at=requested_at,
        issuer=issuer,
        as_of_date=requested_at.date(),
        quarters=8,
        include_promoter_pledge=True,
    )
    snapshots = []
    for request, statement in (
        (overview_request, FundamentalStatement.COMPANY_PROFILE),
        (financials_request, FundamentalStatement.INCOME_STATEMENT),
        (shareholding_request, FundamentalStatement.OWNERSHIP),
    ):
        retrieval = FundamentalEvidenceRetrieval(
            **fundamental_response_values(
                request,
                capability=request.capability,
                issuer=issuer,
                status=FundamentalRetrievalStatus.COMPLETED,
                snapshot=build_snapshot(
                    statement,
                    connection=selected_connection,
                    issuer=issuer,
                    assembled_at=requested_at + timedelta(seconds=1),
                ),
                started_at=requested_at,
                completed_at=completed_at,
            )
        )
        snapshots.append(
            stored_fundamental_snapshot(
                request,
                retrieval,
                stored_at=stored_at,
            )
        )

    document_scenarios = (
        (FinancialDocumentType.GROWTH_TABLE, FinancialReportingBasis.NOT_APPLICABLE),
        *(
            (document_type, reporting_basis)
            for document_type in (
                FinancialDocumentType.BALANCE_SHEET,
                FinancialDocumentType.PROFIT_AND_LOSS,
                FinancialDocumentType.CASH_FLOW,
                FinancialDocumentType.RATIOS,
                FinancialDocumentType.QUARTERLY_RESULTS,
            )
            for reporting_basis in (
                FinancialReportingBasis.CONSOLIDATED,
                FinancialReportingBasis.STANDALONE,
            )
        ),
    )
    structured_documents = []
    for index, (document_type, reporting_basis) in enumerate(document_scenarios):
        request = financial_document_request(
            request_id=f"request.coforge.document.{sequence}.{index}",
            operation_id=operation_id,
            connection=selected_connection,
            requested_at=requested_at,
            issuer=issuer,
            as_of_date=requested_at.date(),
            document_type=document_type,
            reporting_basis=reporting_basis,
        )
        result = financial_document_result(request)
        structured_documents.append(
            stored_structured_financial_document(
                request,
                result,
                stored_at=stored_at,
            )
        )

    peer_request = FundamentalCompanyOverviewRequest(
        request_id=f"request.coforge.peers.{sequence}",
        operation_id=operation_id,
        connection=selected_connection,
        requested_at=requested_at,
        issuer=issuer,
        as_of_date=requested_at.date(),
    )
    peer_values = peer_result_values(peer_request)
    peer_values.update(
        started_at=requested_at,
        completed_at=completed_at,
        document=peer_document(
            connection=selected_connection,
            issuer=issuer,
            observation_date=requested_at.date(),
            retrieved_at=requested_at + timedelta(seconds=1),
            expires_at=requested_at + timedelta(days=10),
        ),
    )
    peer_comparison = stored_peer_comparison_document(
        peer_request,
        FundamentalPeerComparisonResult(**peer_values),
        stored_at=stored_at,
    )

    benchmarking_request = FundamentalCompanyOverviewRequest(
        request_id=f"request.coforge.benchmarking.{sequence}",
        operation_id=operation_id,
        connection=selected_connection,
        requested_at=requested_at,
        issuer=issuer,
        as_of_date=requested_at.date(),
    )
    benchmarking_values = benchmarking_result_values(benchmarking_request)
    benchmarking_values.update(
        started_at=requested_at,
        completed_at=completed_at,
        document=benchmarking_document(
            connection=selected_connection,
            issuer=issuer,
            observation_date=requested_at.date(),
            retrieved_at=requested_at + timedelta(seconds=1),
            expires_at=requested_at + timedelta(days=10),
        ),
    )
    benchmarking = stored_benchmarking_financials_document(
        benchmarking_request,
        FundamentalBenchmarkingFinancialsResult(**benchmarking_values),
        stored_at=stored_at,
    )

    artifacts = (
        *snapshots,
        *structured_documents,
        peer_comparison,
        benchmarking,
    )
    items = tuple(
        FundamentalPackageItem(
            key=key,
            cache_entry_id=artifact.cache_key.cache_entry_id,
            content_fingerprint=(
                artifact.snapshot_fingerprint
                if hasattr(artifact, "snapshot_fingerprint")
                else artifact.document_fingerprint
            ),
            retrieved_at=artifact.retrieved_at,
            expires_at=artifact.expires_at,
        )
        for key, artifact in zip(
            FUNDAMENTAL_PACKAGE_REQUIRED_ITEMS,
            artifacts,
            strict=True,
        )
    )
    manifest = FundamentalPackageManifest(
        operation_id=operation_id,
        connection=selected_connection,
        issuer=issuer,
        requested_at=requested_at,
        committed_at=requested_at + timedelta(seconds=4),
        items=items,
    )
    return FundamentalEvidencePackage(
        manifest=manifest,
        snapshots=tuple(snapshots),
        structured_documents=tuple(structured_documents),
        peer_comparison=peer_comparison,
        benchmarking_financials=benchmarking,
    )


def _detailed_backtest_result():
    base_time = datetime(2026, 1, 1, tzinfo=IST)

    def candle(day, *, high=101.0, low=99.0):
        return Candle(
            timestamp=base_time + timedelta(days=day),
            open=100.0,
            high=high,
            low=low,
            close=100.0,
            volume=1_000 + day,
        )

    candles = [candle(day) for day in range(11)]
    candles[3] = candle(3, high=109, low=99)
    candles[4] = candle(4, high=101, low=91)
    candles[5] = candle(5, high=101, low=95)
    candles[9] = candle(9, high=109, low=99)
    directions = {
        2: SignalDirection.BULLISH,
        3: SignalDirection.BEARISH,
        4: SignalDirection.BULLISH,
        6: SignalDirection.BULLISH,
    }
    series = HistoricalCandleSeries(
        exchange="NSE",
        symbol_token="2885",
        symbol="RELIANCE-EQ",
        interval="ONE_DAY",
        candles=candles,
        retrieved_at=base_time + timedelta(days=30),
        source="test",
    )
    execution = ExecutionSimulationConfig(
        initial_capital=100_000.0,
        risk_per_trade_percentage=1.0,
        maximum_position_percentage=100.0,
        slippage_basis_points=0.0,
        commission_basis_points=0.0,
        fixed_fee_per_order=0.0,
        target_policy=ExecutionTargetPolicy.MINIMUM,
    )
    return run_walk_forward_backtest(
        series,
        orchestrator=_ScriptedOrchestrator(directions),
        config=WalkForwardBacktestConfig(
            warmup_candles=2,
            execution=execution,
        ),
    )


class DuckDBStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "jarvis.duckdb"
        self.stored_at = datetime(2026, 8, 19, 15, 30, tzinfo=IST)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def storage(self) -> DuckDBJarvisStorage:
        return DuckDBJarvisStorage(self.database)

    def test_implements_storage_contract_and_creates_versioned_schema(self):
        with self.storage() as storage:
            self.assertIsInstance(storage, JarvisStorageAdapter)
            self.assertEqual(storage.adapter_name, "duckdb")
            self.assertEqual(storage.schema_version, 9)
            self.assertEqual(storage.database, str(self.database))

        connection = duckdb.connect(str(self.database), read_only=True)
        try:
            version = connection.execute(
                "SELECT metadata_value FROM jarvis_storage_metadata "
                "WHERE metadata_key = 'schema_version'"
            ).fetchone()
            tables = {
                row[0]
                for row in connection.execute("SHOW TABLES").fetchall()
            }
        finally:
            connection.close()

        self.assertEqual(version, ("9",))
        self.assertTrue(
            {
                "jarvis_instruments",
                "jarvis_instrument_symbols",
                "jarvis_market_series",
                "jarvis_market_candles",
                "jarvis_strategy_configurations",
                "jarvis_strategy_weights",
                "jarvis_backtest_runs",
                "jarvis_backtest_evaluations",
                "jarvis_backtest_evaluation_categories",
                "jarvis_backtest_signal_evidence",
                "jarvis_backtest_signal_contributions",
                "jarvis_backtest_trades",
                "jarvis_backtest_equity_points",
                "jarvis_backtest_performance",
                "jarvis_backtest_performance_segments",
                "jarvis_debate_runs",
                "jarvis_debate_signal_signature",
                "jarvis_fundamental_snapshots",
                "jarvis_fundamental_request_statements",
                "jarvis_fundamental_request_period_types",
                "jarvis_fundamental_sources",
                "jarvis_fundamental_facts",
                "jarvis_fundamental_conflicts",
                "jarvis_fundamental_packages",
                "jarvis_active_fundamental_packages",
                "jarvis_fundamental_filing_artifacts",
                "jarvis_fundamental_filing_packages",
                "jarvis_fundamental_filing_package_artifacts",
                "jarvis_active_fundamental_filing_packages",
                "jarvis_fundamental_filing_delete_receipts",
                "jarvis_fundamental_filing_evidence",
                "jarvis_corporate_filings_analysis",
                "jarvis_active_corporate_filings_analysis",
                "jarvis_corporate_filings_analysis_active_history",
                "jarvis_corporate_filings_analysis_delete_receipts",
            }.issubset(tables)
        )

    def test_fundamental_package_round_trip_and_defensive_copies(self):
        package = _fundamental_package()
        now = package.manifest.committed_at + timedelta(minutes=1)
        scope = FundamentalPackageRepositoryScope.from_package(package)
        with DuckDBJarvisStorage(self.database, clock=lambda: now) as storage:
            self.assertIsInstance(storage, FundamentalPackageRepository)
            committed = storage.commit_fundamental_package(
                package,
                expected_active_package_id=None,
            )
            active = storage.get_active_fundamental_package(
                scope=scope,
                as_of=now,
            )

        with DuckDBJarvisStorage(self.database, clock=lambda: now) as reopened:
            resolved = reopened.get_fundamental_package(
                package.package_id,
                scope=scope,
                as_of=now,
            )

        self.assertEqual(committed, package)
        self.assertEqual(active, package)
        self.assertEqual(resolved, package)
        self.assertIsNot(committed, package)
        self.assertIsNot(active, committed)
        self.assertIsNot(active.snapshots[0], committed.snapshots[0])

    def test_fundamental_package_cas_conflict_rolls_back_every_write(self):
        first = _fundamental_package(sequence=1)
        second = _fundamental_package(sequence=2)
        now = second.manifest.committed_at + timedelta(minutes=1)
        scope = FundamentalPackageRepositoryScope.from_package(first)
        with DuckDBJarvisStorage(self.database, clock=lambda: now) as storage:
            storage.commit_fundamental_package(
                first,
                expected_active_package_id=None,
            )
            with self.assertRaises(StorageConflictError):
                storage.commit_fundamental_package(
                    second,
                    expected_active_package_id=(
                        "fundamental_package:" + "0" * 64
                    ),
                )
            active_after_conflict = storage.get_active_fundamental_package(
                scope=scope,
                as_of=now,
            )
            rejected = storage.get_fundamental_package(
                second.package_id,
                scope=scope,
                as_of=now,
            )
            committed = storage.commit_fundamental_package(
                second,
                expected_active_package_id=first.package_id,
            )

        self.assertEqual(active_after_conflict, first)
        self.assertIsNone(rejected)
        self.assertEqual(committed, second)

    def test_fundamental_packages_are_scope_isolated(self):
        first = _fundamental_package(sequence=1)
        other = _fundamental_package(
            sequence=2,
            connection=build_connection(
                tenant_id="tenant.other",
                provider_connection_id="provider.tijori.other",
            ),
        )
        now = other.manifest.committed_at + timedelta(minutes=1)
        first_scope = FundamentalPackageRepositoryScope.from_package(first)
        other_scope = FundamentalPackageRepositoryScope.from_package(other)
        with DuckDBJarvisStorage(self.database, clock=lambda: now) as storage:
            storage.commit_fundamental_package(
                first,
                expected_active_package_id=None,
            )
            storage.commit_fundamental_package(
                other,
                expected_active_package_id=None,
            )

            self.assertEqual(
                storage.get_active_fundamental_package(
                    scope=first_scope,
                    as_of=now,
                ),
                first,
            )
            self.assertEqual(
                storage.get_active_fundamental_package(
                    scope=other_scope,
                    as_of=now,
                ),
                other,
            )
            self.assertIsNone(
                storage.get_fundamental_package(
                    first.package_id,
                    scope=other_scope,
                    as_of=now,
                )
            )

    def test_fundamental_package_expiry_removes_payload_and_pointer(self):
        package = _fundamental_package()
        scope = FundamentalPackageRepositoryScope.from_package(package)
        clock_value = [package.manifest.committed_at + timedelta(minutes=1)]
        with DuckDBJarvisStorage(
            self.database,
            clock=lambda: clock_value[0],
        ) as storage:
            storage.commit_fundamental_package(
                package,
                expected_active_package_id=None,
            )
            clock_value[0] = package.manifest.expires_at
            self.assertEqual(
                storage.purge_expired_fundamental_packages(
                    as_of=clock_value[0]
                ),
                1,
            )
            self.assertIsNone(
                storage.get_active_fundamental_package(
                    scope=scope,
                    as_of=clock_value[0],
                )
            )
            counts = storage._connection.execute(
                "SELECT "
                "(SELECT count(*) FROM jarvis_fundamental_packages), "
                "(SELECT count(*) FROM jarvis_active_fundamental_packages)"
            ).fetchone()

        self.assertEqual(counts, (0, 0))

    def test_filing_package_round_trip_is_metadata_only_and_defensive(self):
        package = filing_package()
        artifact = package.artifacts[0]
        scope = FundamentalFilingRepositoryScope.from_package(package)
        now = package.manifest.committed_at + timedelta(hours=3)

        with DuckDBJarvisStorage(self.database, clock=lambda: now) as storage:
            self.assertIsInstance(storage, FundamentalFilingRepository)
            committed = storage.commit_filing_package(
                package,
                expected_active_package_id=None,
            )
            active = storage.get_active_filing_package(scope=scope)
            by_hash = storage.find_filing_artifacts_by_content_hash(
                PDF_HASH,
                scope=scope,
            )
            versions = storage.list_filing_artifact_versions(
                artifact.reference.filing_reference_id,
                scope=scope,
            )
            column_types = {
                row[1]: row[2]
                for row in storage._connection.execute(
                    "PRAGMA table_info('jarvis_fundamental_filing_artifacts')"
                ).fetchall()
            }

        with DuckDBJarvisStorage(self.database, clock=lambda: now) as reopened:
            resolved = reopened.get_filing_package(
                package.package_id,
                scope=scope,
            )
            resolved_artifact = reopened.get_filing_artifact(
                artifact.artifact_id,
                scope=scope,
            )

        self.assertEqual(committed, package)
        self.assertEqual(active, package)
        self.assertEqual(resolved, package)
        self.assertEqual(resolved_artifact, artifact)
        self.assertEqual(by_hash, (artifact,))
        self.assertEqual(versions, (artifact,))
        self.assertIsNot(committed, package)
        self.assertIsNot(active, committed)
        self.assertIsNot(active.artifacts[0], committed.artifacts[0])
        self.assertNotIn("BLOB", set(column_types.values()))
        self.assertIn("safe_relative_path", column_types)

    def test_filing_evidence_round_trip_preserves_complete_json_and_indexes(self):
        record = stored_filing_evidence()
        now = record.stored_at + timedelta(hours=1)

        with DuckDBJarvisStorage(self.database, clock=lambda: now) as storage:
            self.assertIsInstance(
                storage,
                FundamentalFilingEvidenceRepository,
            )
            saved = storage.save_filing_evidence(record)
        with DuckDBJarvisStorage(self.database, clock=lambda: now) as reopened:
            loaded = reopened.get_filing_evidence(
                record.record_id,
                scope=record.scope,
                as_of=now,
            )
            row = reopened._connection.execute(
                "SELECT tenant_id, scope_fingerprint, artifact_fingerprint, "
                "extraction_policy_fingerprint, "
                "validation_policy_fingerprint, extraction_fingerprint, "
                "validation_fingerprint, payload_json "
                "FROM jarvis_fundamental_filing_evidence "
                "WHERE record_id = ?",
                [record.record_id],
            ).fetchone()
            indexes = {
                row[0]
                for row in reopened._connection.execute(
                    "SELECT index_name FROM duckdb_indexes() "
                    "WHERE table_name = "
                    "'jarvis_fundamental_filing_evidence'"
                ).fetchall()
            }

        decoded_payload = json.loads(row[7])
        self.assertEqual(saved, record)
        self.assertEqual(loaded, record)
        self.assertIsNot(saved, record)
        self.assertIsNot(loaded, saved)
        self.assertEqual(row[0], record.scope.connection.tenant_id)
        self.assertEqual(len(row[1]), 64)
        self.assertEqual(
            row[2:7],
            (
                record.artifact_fingerprint,
                record.extraction_policy_fingerprint,
                record.validation_policy_fingerprint,
                record.extraction_fingerprint,
                record.validation_fingerprint,
            ),
        )
        self.assertEqual(
            set(decoded_payload),
            {
                "schema_version",
                "artifact_id",
                "artifact_fingerprint",
                "extraction_policy_fingerprint",
                "validation_policy_fingerprint",
                "extraction_fingerprint",
                "validation_fingerprint",
                "stored_at",
                "evidence",
                "validation",
            },
        )
        self.assertEqual(
            indexes,
            {
                "jarvis_filing_evidence_scope_lookup",
                "jarvis_filing_evidence_policy_lookup",
                "jarvis_filing_evidence_lineage_lookup",
            },
        )

    def test_filing_evidence_is_idempotent_and_conflict_rolls_back(self):
        record = stored_filing_evidence()
        conflicting = record.model_copy(
            update={"stored_at": record.stored_at + timedelta(seconds=1)}
        )
        now = conflicting.stored_at + timedelta(hours=1)

        with DuckDBJarvisStorage(self.database, clock=lambda: now) as storage:
            first = storage.save_filing_evidence(record)
            repeated = storage.save_filing_evidence(record)
            with self.assertRaises(StorageConflictError):
                storage.save_filing_evidence(conflicting)
            count = storage._connection.execute(
                "SELECT count(*) "
                "FROM jarvis_fundamental_filing_evidence"
            ).fetchone()[0]
            retained = storage.get_filing_evidence(
                record.record_id,
                scope=record.scope,
                as_of=now,
            )

        self.assertEqual(first, repeated)
        self.assertEqual(count, 1)
        self.assertEqual(retained, record)

    def test_filing_evidence_policy_reads_are_point_in_time_and_scoped(self):
        first = stored_filing_evidence()
        second = revised_filing_evidence_record(minutes=2)
        key = FundamentalFilingEvidencePolicyKey.from_record(first)
        now = second.stored_at + timedelta(hours=1)
        other_scope = FundamentalFilingRepositoryScope(
            connection=build_connection(
                tenant_id="tenant.other",
                provider_connection_id="provider.tijori.other",
            ),
            issuer=first.scope.issuer,
        )

        with DuckDBJarvisStorage(self.database, clock=lambda: now) as storage:
            storage.save_filing_evidence(first)
            storage.save_filing_evidence(second)
            before_second = storage.find_filing_evidence_by_policy(
                key,
                scope=first.scope,
                as_of=second.stored_at - timedelta(microseconds=1),
            )
            versions = storage.list_filing_evidence_for_artifact(
                first.artifact_id,
                scope=first.scope,
                as_of=now,
            )
            cross_tenant = storage.find_filing_evidence_by_policy(
                key,
                scope=other_scope,
                as_of=now,
            )

        self.assertEqual(before_second, (first,))
        self.assertEqual(versions, (first, second))
        self.assertEqual(cross_tenant, ())

    def test_corporate_analysis_round_trip_and_exact_reuse_index(self):
        record = corporate_analysis_record()
        now = record.stored_at + timedelta(hours=1)

        with DuckDBJarvisStorage(self.database, clock=lambda: now) as storage:
            self.assertIsInstance(
                storage,
                CorporateFilingsAnalysisRepository,
            )
            saved = storage.save_corporate_filings_analysis(record)
            repeated = storage.save_corporate_filings_analysis(record)
            with self.assertRaisesRegex(StorageConflictError, "record ID"):
                storage.save_corporate_filings_analysis(
                    record.model_copy(
                        update={
                            "stored_at": record.stored_at
                            + timedelta(seconds=1)
                        }
                    )
                )
            same_reuse_different_result = record.model_copy(
                update={
                    "result": record.result.model_copy(
                        update={
                            "analysis_id": "corporate-filing-analysis-revised"
                        }
                    )
                }
            )
            with self.assertRaisesRegex(StorageConflictError, "reuse key"):
                storage.save_corporate_filings_analysis(
                    same_reuse_different_result
                )
            before_storage = (
                storage.find_corporate_filings_analysis_by_reuse_key(
                    record.reuse_key,
                    scope=record.scope,
                    as_of=record.stored_at - timedelta(microseconds=1),
                )
            )

        with DuckDBJarvisStorage(self.database, clock=lambda: now) as reopened:
            cached = reopened.find_corporate_filings_analysis_by_reuse_key(
                record.reuse_key,
                scope=record.scope,
                as_of=now,
            )
            row = reopened._connection.execute(
                "SELECT scope_fingerprint, period_key_fingerprint, "
                "reuse_key_fingerprint, filing_package_fingerprint, "
                "bundle_fingerprint, policy_fingerprint, "
                "model_configuration_fingerprint, payload_json "
                "FROM jarvis_corporate_filings_analysis "
                "WHERE record_id = ?",
                [record.record_id],
            ).fetchone()
            indexes = {
                item[0]
                for item in reopened._connection.execute(
                    "SELECT index_name FROM duckdb_indexes() "
                    "WHERE table_name = "
                    "'jarvis_corporate_filings_analysis'"
                ).fetchall()
            }

        payload = json.loads(row[7])
        self.assertEqual(saved, record)
        self.assertEqual(repeated, record)
        self.assertEqual(cached, record)
        self.assertIsNot(saved, record)
        self.assertIsNot(repeated, saved)
        self.assertIsNot(cached, saved)
        self.assertIsNone(before_storage)
        self.assertEqual(
            row[:7],
            (
                record.scope.scope_fingerprint,
                record.period_key.period_key_fingerprint,
                record.reuse_key.reuse_key_fingerprint,
                record.filing_package_fingerprint,
                record.bundle_fingerprint,
                record.policy_fingerprint,
                record.model_configuration_fingerprint,
            ),
        )
        self.assertNotIn("bundle", payload)
        self.assertNotIn("prompt", payload)
        self.assertIn("result", payload)
        self.assertEqual(
            indexes,
            {
                "jarvis_corporate_analysis_reuse_lookup",
                "jarvis_corporate_analysis_period_history_lookup",
                "jarvis_corporate_analysis_lineage_lookup",
            },
        )

    def test_corporate_analysis_cas_is_atomic_and_history_is_point_in_time(self):
        first = corporate_analysis_record()
        candidate = corporate_analysis_record(
            prompt="prompt-b",
            stored_at=first.stored_at + timedelta(minutes=10),
        )
        clock_value = [first.stored_at + timedelta(hours=1)]
        first_activation = clock_value[0]

        with DuckDBJarvisStorage(
            self.database,
            clock=lambda: clock_value[0],
        ) as storage:
            storage.commit_corporate_filings_analysis(
                first,
                expected_active_record_id=None,
            )
            with self.assertRaisesRegex(
                StorageConflictError,
                "changed before commit",
            ):
                storage.commit_corporate_filings_analysis(
                    candidate,
                    expected_active_record_id=None,
                )
            self.assertIsNone(
                storage.find_corporate_filings_analysis_by_reuse_key(
                    candidate.reuse_key,
                    scope=candidate.scope,
                    as_of=clock_value[0],
                )
            )
            self.assertEqual(
                storage.get_active_corporate_filings_analysis(
                    first.period_key,
                    scope=first.scope,
                    as_of=clock_value[0],
                ),
                first,
            )

            clock_value[0] += timedelta(minutes=1)
            second_activation = clock_value[0]
            storage.commit_corporate_filings_analysis(
                candidate,
                expected_active_record_id=first.record_id,
            )
            before_replacement = (
                storage.get_active_corporate_filings_analysis(
                    first.period_key,
                    scope=first.scope,
                    as_of=first_activation + timedelta(seconds=30),
                )
            )
            after_replacement = (
                storage.get_active_corporate_filings_analysis(
                    first.period_key,
                    scope=first.scope,
                    as_of=second_activation,
                )
            )
            history = storage.list_corporate_filings_analysis_history(
                first.period_key,
                scope=first.scope,
                as_of=second_activation,
            )
            counts = storage._connection.execute(
                "SELECT "
                "(SELECT count(*) "
                "FROM jarvis_corporate_filings_analysis), "
                "(SELECT count(*) "
                "FROM jarvis_active_corporate_filings_analysis), "
                "(SELECT count(*) FROM "
                "jarvis_corporate_filings_analysis_active_history)"
            ).fetchone()

        self.assertEqual(before_replacement, first)
        self.assertEqual(after_replacement, candidate)
        self.assertEqual(history, (first, candidate))
        self.assertEqual(counts, (2, 1, 2))

    def test_corporate_analysis_reads_are_tenant_isolated(self):
        first = corporate_analysis_record()
        other = corporate_analysis_record(tenant_id="tenant-2")
        now = first.stored_at + timedelta(hours=1)

        with DuckDBJarvisStorage(self.database, clock=lambda: now) as storage:
            storage.commit_corporate_filings_analysis(
                first,
                expected_active_record_id=None,
            )
            self.assertIsNone(
                storage.find_corporate_filings_analysis_by_reuse_key(
                    first.reuse_key,
                    scope=other.scope,
                    as_of=now,
                )
            )
            self.assertEqual(
                storage.list_corporate_filings_analysis_history(
                    first.period_key,
                    scope=other.scope,
                    as_of=now,
                ),
                (),
            )
            self.assertIsNone(
                storage.get_active_corporate_filings_analysis(
                    first.period_key,
                    scope=other.scope,
                    as_of=now,
                )
            )

    def test_corporate_analysis_deletion_is_audited_and_preserves_active(self):
        first = corporate_analysis_record()
        candidate = corporate_analysis_record(
            prompt="prompt-b",
            stored_at=first.stored_at + timedelta(minutes=10),
        )
        clock_value = [first.stored_at + timedelta(hours=1)]

        with DuckDBJarvisStorage(
            self.database,
            clock=lambda: clock_value[0],
        ) as storage:
            storage.commit_corporate_filings_analysis(
                first,
                expected_active_record_id=None,
            )
            rejected = storage.delete_corporate_filings_analysis(
                first.record_id,
                scope=first.scope,
                authorization=corporate_analysis_delete_authorization(
                    first,
                    audit_id="audit-active",
                    authorized_at=clock_value[0],
                ),
            )
            clock_value[0] += timedelta(minutes=1)
            storage.commit_corporate_filings_analysis(
                candidate,
                expected_active_record_id=first.record_id,
            )
            authorization = corporate_analysis_delete_authorization(
                first,
                audit_id="audit-inactive",
                authorized_at=clock_value[0],
            )
            deleted = storage.delete_corporate_filings_analysis(
                first.record_id,
                scope=first.scope,
                authorization=authorization,
            )
            repeated = storage.delete_corporate_filings_analysis(
                first.record_id,
                scope=first.scope,
                authorization=authorization,
            )
            active = storage.get_active_corporate_filings_analysis(
                candidate.period_key,
                scope=candidate.scope,
                as_of=clock_value[0],
            )
            counts = storage._connection.execute(
                "SELECT "
                "(SELECT count(*) "
                "FROM jarvis_corporate_filings_analysis), "
                "(SELECT count(*) FROM "
                "jarvis_corporate_filings_analysis_active_history), "
                "(SELECT count(*) FROM "
                "jarvis_corporate_filings_analysis_delete_receipts)"
            ).fetchone()

        self.assertEqual(
            rejected.status,
            CorporateFilingsAnalysisDeleteStatus.REJECTED_ACTIVE_RESULT,
        )
        self.assertTrue(deleted.deleted)
        self.assertEqual(repeated, deleted)
        self.assertEqual(active, candidate)
        self.assertEqual(counts, (1, 1, 2))

    def test_filing_package_cas_conflict_rolls_back_and_replace_is_atomic(self):
        first = filing_package()
        second = revised_filing_package(sequence=2)
        scope = FundamentalFilingRepositoryScope.from_package(first)
        now = second.manifest.committed_at + timedelta(hours=3)

        with DuckDBJarvisStorage(self.database, clock=lambda: now) as storage:
            storage.commit_filing_package(
                first,
                expected_active_package_id=None,
            )
            with self.assertRaises(StorageConflictError):
                storage.commit_filing_package(
                    second,
                    expected_active_package_id=(
                        "filing_package:" + "0" * 64
                    ),
                )
            active_after_conflict = storage.get_active_filing_package(
                scope=scope
            )
            rejected_package = storage.get_filing_package(
                second.package_id,
                scope=scope,
            )
            rejected_artifact = storage.get_filing_artifact(
                second.artifacts[0].artifact_id,
                scope=scope,
            )
            committed = storage.commit_filing_package(
                second,
                expected_active_package_id=first.package_id,
            )
            active_after_replace = storage.get_active_filing_package(
                scope=scope
            )
            retained_first = storage.get_filing_package(
                first.package_id,
                scope=scope,
            )

        self.assertEqual(active_after_conflict, first)
        self.assertIsNone(rejected_package)
        self.assertIsNone(rejected_artifact)
        self.assertEqual(committed, second)
        self.assertEqual(active_after_replace, second)
        self.assertEqual(retained_first, first)

    def test_filing_reads_are_scope_isolated_and_recheck_does_not_delete(self):
        first = filing_package()
        other = revised_filing_package(
            sequence=2,
            connection=build_connection(
                tenant_id="tenant.other",
                provider_connection_id="provider.tijori.other",
            ),
            issuer=build_issuer(
                symbol="RELIANCE-EQ",
                legal_name="Reliance Industries Limited",
                isin="INE002A01018",
                provider_company_id="999",
                provider_slug="reliance-industries-limited",
            ),
        )
        first_scope = FundamentalFilingRepositoryScope.from_package(first)
        other_scope = FundamentalFilingRepositoryScope.from_package(other)
        now = max(RECHECK_AFTER, other.manifest.committed_at) + timedelta(hours=1)

        with DuckDBJarvisStorage(self.database, clock=lambda: now) as storage:
            storage.commit_filing_package(
                first,
                expected_active_package_id=None,
            )
            storage.commit_filing_package(
                other,
                expected_active_package_id=None,
            )
            active = storage.get_active_filing_package(scope=first_scope)
            other_active = storage.get_active_filing_package(scope=other_scope)
            cross_scope = storage.get_filing_package(
                first.package_id,
                scope=other_scope,
            )
            cross_hash = storage.find_filing_artifacts_by_content_hash(
                PDF_HASH,
                scope=other_scope,
            )

        self.assertEqual(active, first)
        self.assertTrue(active.manifest.is_recheck_due(as_of=now))
        self.assertEqual(other_active, other)
        self.assertIsNone(cross_scope)
        self.assertEqual(cross_hash, ())

    def test_filing_deletion_is_audited_and_preserves_filesystem_ownership(self):
        package = filing_package()
        artifact = package.artifacts[0]
        scope = FundamentalFilingRepositoryScope.from_package(package)
        now = package.manifest.committed_at + timedelta(hours=3)
        artifact_authorization = authorization_for(artifact)
        package_authorization = authorization_for(package).model_copy(
            update={"audit_id": "audit.filing.delete.package.1"}
        )

        with DuckDBJarvisStorage(self.database, clock=lambda: now) as storage:
            storage.commit_filing_package(
                package,
                expected_active_package_id=None,
            )
            rejected = storage.delete_filing_artifact(
                artifact.artifact_id,
                scope=scope,
                authorization=artifact_authorization,
            )
            deleted_package = storage.delete_filing_package(
                package.package_id,
                scope=scope,
                authorization=package_authorization,
            )
            deleted_artifact = storage.delete_filing_artifact(
                artifact.artifact_id,
                scope=scope,
                authorization=artifact_authorization.model_copy(
                    update={"audit_id": "audit.filing.delete.artifact.2"}
                ),
            )
            repeated = storage.delete_filing_artifact(
                artifact.artifact_id,
                scope=scope,
                authorization=artifact_authorization.model_copy(
                    update={"audit_id": "audit.filing.delete.artifact.2"}
                ),
            )
            counts = storage._connection.execute(
                "SELECT "
                "(SELECT count(*) FROM jarvis_fundamental_filing_artifacts), "
                "(SELECT count(*) FROM jarvis_fundamental_filing_packages), "
                "(SELECT count(*) FROM "
                "jarvis_active_fundamental_filing_packages), "
                "(SELECT count(*) FROM "
                "jarvis_fundamental_filing_delete_receipts)"
            ).fetchone()

        self.assertEqual(
            rejected.status,
            FundamentalFilingDeleteStatus.REJECTED_ACTIVE_REFERENCE,
        )
        self.assertTrue(deleted_package.deleted)
        self.assertTrue(deleted_artifact.deleted)
        self.assertEqual(repeated, deleted_artifact)
        self.assertEqual(counts, (0, 0, 0, 3))

    def test_normalizes_instrument_symbol_and_every_market_candle(self):
        series = _market_series()
        with self.storage() as storage:
            saved = storage.save_market_series(
                stored_market_series(series, stored_at=self.stored_at)
            )
            instrument = storage._connection.execute(
                "SELECT source, exchange, symbol_token, current_symbol "
                "FROM jarvis_instruments"
            ).fetchone()
            dataset = storage._connection.execute(
                "SELECT instrument_id, symbol FROM jarvis_market_series "
                "WHERE dataset_id = ?",
                [saved.dataset_id],
            ).fetchone()
            candles = storage._connection.execute(
                "SELECT candle_index, close, volume "
                "FROM jarvis_market_candles WHERE dataset_id = ? "
                "ORDER BY candle_index",
                [saved.dataset_id],
            ).fetchall()
            symbol_observation = storage._connection.execute(
                "SELECT symbol, dataset_id FROM jarvis_instrument_symbols"
            ).fetchone()

        self.assertEqual(
            instrument,
            ("test", "NSE", "2885", "RELIANCE-EQ"),
        )
        self.assertTrue(dataset[0].startswith("instrument:"))
        self.assertEqual(dataset[1], "RELIANCE-EQ")
        self.assertEqual(candles, [(0, 101.0, 1000), (1, 102.0, 1001)])
        self.assertEqual(
            symbol_observation,
            ("RELIANCE-EQ", saved.dataset_id),
        )

    def test_persists_complete_normalized_backtest_attributes(self):
        result = _detailed_backtest_result()
        stored = stored_backtest_run(
            result,
            run_id="detailed-run",
            stored_at=self.stored_at,
        )
        with self.storage() as storage:
            storage.save_market_series(
                stored_market_series(
                    result.market_series,
                    stored_at=self.stored_at,
                )
            )
            storage.save_backtest_run(stored)
            connection = storage._connection
            run_attributes = connection.execute(
                "SELECT market_dataset_id, instrument_id, symbol, source, "
                "resolved_warmup_candles FROM jarvis_backtest_runs "
                "WHERE run_id = ?",
                [stored.run_id],
            ).fetchone()
            counts = {
                table: connection.execute(
                    f"SELECT count(*) FROM {table} WHERE run_id = ?",
                    [stored.run_id],
                ).fetchone()[0]
                for table in (
                    "jarvis_backtest_evaluations",
                    "jarvis_backtest_evaluation_categories",
                    "jarvis_backtest_signal_evidence",
                    "jarvis_backtest_signal_contributions",
                    "jarvis_backtest_trades",
                    "jarvis_backtest_equity_points",
                    "jarvis_backtest_performance",
                    "jarvis_backtest_performance_segments",
                )
            }
            trade = connection.execute(
                "SELECT direction, outcome, entry_price, target_price, "
                "net_pnl, realized_r_multiple FROM jarvis_backtest_trades "
                "WHERE run_id = ? ORDER BY evaluation_index LIMIT 1",
                [stored.run_id],
            ).fetchone()
            performance = connection.execute(
                "SELECT entered_trades, winning_trades, net_profit, "
                "maximum_drawdown_amount FROM jarvis_backtest_performance "
                "WHERE run_id = ?",
                [stored.run_id],
            ).fetchone()

        self.assertTrue(run_attributes[0].startswith("market:"))
        self.assertTrue(run_attributes[1].startswith("instrument:"))
        self.assertEqual(run_attributes[2:], ("RELIANCE-EQ", "test", 2))
        self.assertEqual(
            counts,
            {
                "jarvis_backtest_evaluations": 9,
                "jarvis_backtest_evaluation_categories": 42,
                "jarvis_backtest_signal_evidence": 42,
                "jarvis_backtest_signal_contributions": 42,
                "jarvis_backtest_trades": 4,
                "jarvis_backtest_equity_points": 11,
                "jarvis_backtest_performance": 1,
                "jarvis_backtest_performance_segments": 4,
            },
        )
        self.assertEqual(trade[:2], ("long", "minimum_target"))
        self.assertEqual(trade[2:5], (100.0, 108.0, 2000.0))
        self.assertAlmostEqual(trade[5], 2.0)
        self.assertEqual(performance[:3], (4, 3, 5056.0))
        self.assertGreater(performance[3], 0)

    def test_persists_reproducible_strategy_snapshot_and_weights(self):
        result = WalkForwardBacktestEngine(
            config=WalkForwardBacktestConfig(warmup_candles=2)
        ).run(_market_series())
        stored = stored_backtest_run(
            result,
            stored_at=self.stored_at,
        )
        with self.storage() as storage:
            storage.save_backtest_run(stored)
            strategy = storage._connection.execute(
                "SELECT strategy_configuration_id, configuration_json "
                "FROM jarvis_strategy_configurations"
            ).fetchone()
            weights = storage._connection.execute(
                "SELECT category, weight FROM jarvis_strategy_weights "
                "ORDER BY category"
            ).fetchall()
            run_strategy_id = storage._connection.execute(
                "SELECT strategy_configuration_id "
                "FROM jarvis_backtest_runs WHERE run_id = ?",
                [stored.run_id],
            ).fetchone()[0]

        snapshot = json.loads(strategy[1])
        self.assertEqual(run_strategy_id, strategy[0])
        self.assertEqual(
            strategy[0],
            result.strategy_configuration.strategy_configuration_id,
        )
        self.assertIn("technical_parameters", snapshot)
        self.assertIn("trade_planning_parameters", snapshot)
        self.assertIn("execution_parameters", snapshot)
        self.assertIn("walk_forward_parameters", snapshot)
        self.assertEqual(
            weights,
            [
                ("candlestick", 1.0),
                ("momentum", 1.0),
                ("price_action", 1.5),
                ("trend", 1.25),
                ("volatility", 0.75),
                ("volume", 1.0),
            ],
        )

    def test_market_series_survives_close_and_reopen(self):
        with self.storage() as storage:
            archive = ResearchArchiveService(storage)
            saved = archive.archive_market_series(
                _market_series(),
                stored_at=self.stored_at,
            )

        with self.storage() as reopened:
            loaded = reopened.get_market_series(saved.dataset_id)

        self.assertEqual(loaded, saved)
        self.assertEqual(loaded.stored_at.utcoffset(), timedelta(hours=5.5))

    def test_market_history_request_coverage_survives_reopen(self):
        requested_from = datetime(
            2016, 9, 7, 9, 15, tzinfo=ZoneInfo("Asia/Kolkata")
        )
        requested_to = datetime(
            2026, 9, 10, 15, 30, tzinfo=ZoneInfo("Asia/Kolkata")
        )
        series = _market_series().model_copy(
            update={
                "history_requested_from": requested_from,
                "history_requested_to": requested_to,
            }
        )
        with self.storage() as storage:
            saved = ResearchArchiveService(storage).archive_market_series(
                series,
                stored_at=self.stored_at,
            )

        with self.storage() as reopened:
            loaded = reopened.get_market_series(saved.dataset_id)

        self.assertEqual(loaded.series.history_requested_from, requested_from)
        self.assertEqual(loaded.series.history_requested_to, requested_to)
        self.assertEqual(loaded, saved)

    def test_backtest_and_market_aggregate_survive_restart(self):
        with self.storage() as storage:
            archive = ResearchArchiveService(storage)
            saved = archive.archive_backtest_run(
                _backtest_result(),
                stored_at=self.stored_at,
            )

        with self.storage() as reopened:
            archive = ResearchArchiveService(reopened)
            loaded = archive.get_backtest_run(saved.run_id)
            market_summaries = archive.list_market_series()
            backtest_summaries = archive.list_backtest_runs()

        self.assertEqual(loaded, saved)
        self.assertEqual(len(market_summaries), 1)
        self.assertEqual(len(backtest_summaries), 1)
        self.assertEqual(backtest_summaries[0].symbol, "RELIANCE-EQ")
        self.assertEqual(backtest_summaries[0].candle_count, 2)

    def test_multi_timeframe_hierarchy_survives_duckdb_json_round_trip(self):
        result = _multi_timeframe_use_case().execute(
            "NSE",
            "2885",
            "RELIANCE-EQ",
            "ONE_HOUR",
        )
        expected_assessment = result.trade_plan_result.hierarchy_assessment
        self.assertIsNotNone(expected_assessment)
        self.assertEqual(
            result.interpretation.hierarchy_assessment,
            expected_assessment,
        )

        connection = duckdb.connect(str(self.database))
        try:
            connection.execute(
                "CREATE TABLE hierarchy_round_trip ("
                "operation_id VARCHAR PRIMARY KEY, payload_json VARCHAR NOT NULL"
                ")"
            )
            connection.execute(
                "INSERT INTO hierarchy_round_trip VALUES (?, ?)",
                ["operation-1", result.model_dump_json()],
            )
        finally:
            connection.close()

        reopened = duckdb.connect(str(self.database), read_only=True)
        try:
            payload_json = reopened.execute(
                "SELECT payload_json FROM hierarchy_round_trip "
                "WHERE operation_id = ?",
                ["operation-1"],
            ).fetchone()[0]
        finally:
            reopened.close()

        loaded = MultiTimeframeEndToEndSwingAnalysisResult.model_validate_json(
            payload_json
        )
        self.assertEqual(loaded, result)
        self.assertEqual(
            loaded.trade_plan_result.hierarchy_assessment,
            expected_assessment,
        )
        self.assertEqual(
            loaded.interpretation.hierarchy_assessment,
            expected_assessment,
        )

    def test_run_and_archive_use_case_persists_to_duckdb(self):
        runner = WalkForwardBacktestEngine(
            config=WalkForwardBacktestConfig(warmup_candles=2)
        )
        with self.storage() as storage:
            use_case = RunAndArchiveWalkForwardBacktest(
                ResearchArchiveService(storage),
                runner,
            )
            receipt = use_case.execute(
                _market_series(),
                stored_at=self.stored_at,
            )

        with self.storage() as reopened:
            loaded = reopened.get_backtest_run(receipt.run_id)

        self.assertEqual(loaded, receipt.backtest_run)

    def test_repeated_save_is_idempotent_and_preserves_first_timestamp(self):
        series = _market_series()
        first = stored_market_series(
            series,
            stored_at=self.stored_at,
        )
        repeated = stored_market_series(
            series,
            stored_at=self.stored_at + timedelta(hours=1),
        )
        with self.storage() as storage:
            first_result = storage.save_market_series(first)
            repeated_result = storage.save_market_series(repeated)

        self.assertEqual(repeated_result, first_result)
        self.assertEqual(repeated_result.stored_at, self.stored_at)

    def test_conflicting_identifier_rolls_back_without_overwrite(self):
        original = stored_market_series(
            _market_series(),
            dataset_id="shared-id",
            stored_at=self.stored_at,
        )
        conflict = stored_market_series(
            _market_series(symbol_token="1333", symbol="HDFCBANK-EQ"),
            dataset_id="shared-id",
            stored_at=self.stored_at,
        )
        with self.storage() as storage:
            storage.save_market_series(original)
            with self.assertRaises(StorageConflictError):
                storage.save_market_series(conflict)
            loaded = storage.get_market_series("shared-id")

        self.assertEqual(loaded, original)

    def test_queries_use_all_filters_time_bounds_and_pagination(self):
        with self.storage() as storage:
            archive = ResearchArchiveService(storage)
            archive.archive_market_series(
                _market_series(symbol_token="1333", symbol="HDFCBANK-EQ"),
                dataset_id="hdfc",
                stored_at=self.stored_at,
            )
            archive.archive_market_series(
                _market_series(day_offset=1),
                dataset_id="reliance-old",
                stored_at=self.stored_at + timedelta(hours=1),
            )
            archive.archive_market_series(
                _market_series(day_offset=2),
                dataset_id="reliance-new",
                stored_at=self.stored_at + timedelta(hours=2),
            )

            results = archive.list_market_series(
                MarketSeriesQuery(
                    exchange="NSE",
                    symbol_token="2885",
                    interval="ONE_DAY",
                    source="test",
                    stored_from=self.stored_at + timedelta(minutes=30),
                    stored_to=self.stored_at + timedelta(hours=3),
                    limit=1,
                    offset=1,
                )
            )

        self.assertEqual(
            [summary.dataset_id for summary in results],
            ["reliance-old"],
        )

    def test_backtest_query_filters_are_applied_by_database(self):
        reliance = stored_backtest_run(
            _backtest_result(),
            run_id="reliance",
            stored_at=self.stored_at,
        )
        hdfc = stored_backtest_run(
            _backtest_result(
                symbol_token="1333",
                symbol="HDFCBANK-EQ",
            ),
            run_id="hdfc",
            stored_at=self.stored_at + timedelta(hours=1),
        )
        with self.storage() as storage:
            storage.save_backtest_run(reliance)
            storage.save_backtest_run(hdfc)
            results = storage.list_backtest_runs(
                BacktestRunQuery(
                    backtest_id=reliance.result.backtest_id,
                    engine_id=reliance.result.engine_id,
                    exchange="NSE",
                    symbol_token="2885",
                    interval="ONE_DAY",
                )
            )

        self.assertEqual([summary.run_id for summary in results], ["reliance"])

    def test_delete_result_persists_and_missing_delete_is_false(self):
        stored = stored_backtest_run(
            _backtest_result(),
            stored_at=self.stored_at,
        )
        with self.storage() as storage:
            storage.save_backtest_run(stored)
            self.assertTrue(storage.delete_backtest_run(stored.run_id))
            self.assertFalse(storage.delete_backtest_run(stored.run_id))

        with self.storage() as reopened:
            self.assertIsNone(reopened.get_backtest_run(stored.run_id))

    def test_parameterized_identifier_cannot_change_query_semantics(self):
        with self.storage() as storage:
            storage.save_market_series(
                stored_market_series(
                    _market_series(),
                    dataset_id="safe-id",
                    stored_at=self.stored_at,
                )
            )

            loaded = storage.get_market_series("x' OR 1=1 --")

        self.assertIsNone(loaded)

    def test_corrupt_payload_is_reported_as_application_storage_error(self):
        with self.storage() as storage:
            stored = storage.save_market_series(
                stored_market_series(
                    _market_series(),
                    stored_at=self.stored_at,
                )
            )

        connection = duckdb.connect(str(self.database))
        try:
            connection.execute(
                "UPDATE jarvis_market_series SET payload_json = ? "
                "WHERE dataset_id = ?",
                ["not-json", stored.dataset_id],
            )
        finally:
            connection.close()

        with self.storage() as reopened:
            with self.assertRaises(StorageError) as raised:
                reopened.get_market_series(stored.dataset_id)

        self.assertIsInstance(raised.exception.__cause__, ValueError)

    def test_rejects_unknown_schema_version_without_modifying_database(self):
        connection = duckdb.connect(str(self.database))
        try:
            connection.execute(
                "CREATE TABLE jarvis_storage_metadata ("
                "metadata_key VARCHAR PRIMARY KEY, "
                "metadata_value VARCHAR NOT NULL)"
            )
            connection.execute(
                "INSERT INTO jarvis_storage_metadata VALUES (?, ?)",
                ["schema_version", "999"],
            )
        finally:
            connection.close()

        with self.assertRaisesRegex(StorageError, "Unsupported.*999"):
            self.storage()

        connection = duckdb.connect(str(self.database), read_only=True)
        try:
            tables = {
                row[0]
                for row in connection.execute("SHOW TABLES").fetchall()
            }
        finally:
            connection.close()
        self.assertNotIn("jarvis_market_series", tables)
        self.assertNotIn("jarvis_backtest_runs", tables)

    def test_migrates_version_one_json_into_normalized_schema(self):
        market = stored_market_series(
            _market_series(),
            stored_at=self.stored_at,
        )
        backtest = stored_backtest_run(
            _backtest_result(),
            run_id="legacy-run",
            stored_at=self.stored_at,
        )
        legacy_backtest_payload = backtest.model_dump(mode="json")
        legacy_backtest_payload.pop("storage_schema_version")
        connection = duckdb.connect(str(self.database))
        try:
            connection.execute(
                "CREATE TABLE jarvis_storage_metadata ("
                "metadata_key VARCHAR PRIMARY KEY, "
                "metadata_value VARCHAR NOT NULL)"
            )
            connection.execute(
                "INSERT INTO jarvis_storage_metadata VALUES (?, ?)",
                ["schema_version", "1"],
            )
            connection.execute(
                "CREATE TABLE jarvis_market_series ("
                "dataset_id VARCHAR PRIMARY KEY, "
                "payload_fingerprint VARCHAR NOT NULL, "
                "stored_at TIMESTAMPTZ NOT NULL, exchange VARCHAR NOT NULL, "
                "symbol_token VARCHAR NOT NULL, interval VARCHAR NOT NULL, "
                "source VARCHAR NOT NULL, summary_json VARCHAR NOT NULL, "
                "payload_json VARCHAR NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE jarvis_backtest_runs ("
                "run_id VARCHAR PRIMARY KEY, "
                "result_fingerprint VARCHAR NOT NULL, "
                "market_fingerprint VARCHAR NOT NULL, "
                "stored_at TIMESTAMPTZ NOT NULL, backtest_id VARCHAR NOT NULL, "
                "engine_id VARCHAR NOT NULL, exchange VARCHAR NOT NULL, "
                "symbol_token VARCHAR NOT NULL, interval VARCHAR NOT NULL, "
                "summary_json VARCHAR NOT NULL, payload_json VARCHAR NOT NULL)"
            )
            series = market.series
            connection.execute(
                "INSERT INTO jarvis_market_series VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    market.dataset_id,
                    market.payload_fingerprint,
                    market.stored_at,
                    series.exchange,
                    series.symbol_token,
                    series.interval,
                    series.source,
                    market_series_summary(market).model_dump_json(),
                    market.model_dump_json(),
                ],
            )
            result = backtest.result
            connection.execute(
                "INSERT INTO jarvis_backtest_runs VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    backtest.run_id,
                    backtest.result_fingerprint,
                    backtest.market_fingerprint,
                    backtest.stored_at,
                    result.backtest_id,
                    result.engine_id,
                    series.exchange,
                    series.symbol_token,
                    series.interval,
                    backtest_run_summary(backtest).model_dump_json(),
                    json.dumps(legacy_backtest_payload),
                ],
            )
        finally:
            connection.close()

        with self.storage() as migrated:
            version = migrated._connection.execute(
                "SELECT metadata_value FROM jarvis_storage_metadata "
                "WHERE metadata_key = 'schema_version'"
            ).fetchone()[0]
            candle_count = migrated._connection.execute(
                "SELECT count(*) FROM jarvis_market_candles"
            ).fetchone()[0]
            equity_count = migrated._connection.execute(
                "SELECT count(*) FROM jarvis_backtest_equity_points"
            ).fetchone()[0]
            loaded = migrated.get_backtest_run("legacy-run")

        self.assertEqual(version, "9")
        self.assertEqual(candle_count, 2)
        self.assertEqual(equity_count, 2)
        self.assertEqual(loaded.run_id, "legacy-run")
        self.assertEqual(loaded.storage_schema_version, 1)

    def test_debate_run_persists_and_survives_close_and_reopen(self):
        result, profile = _build_debate_result()
        stored = stored_debate_run(
            result,
            profile,
            stored_at=datetime(2026, 1, 1, tzinfo=IST),
        )
        with self.storage() as storage:
            saved = storage.save_debate_run(stored)
            self.assertEqual(saved.run_id, stored.run_id)

        with self.storage() as reopened:
            fetched = reopened.get_debate_run(stored.run_id)
            self.assertEqual(fetched, saved)
            summaries = reopened.list_debate_runs()
            self.assertEqual(len(summaries), 1)
            self.assertEqual(summaries[0].run_id, stored.run_id)

    def test_debate_run_conflict_and_delete(self):
        result, profile = _build_debate_result()
        stored = stored_debate_run(
            result,
            profile,
            stored_at=datetime(2026, 1, 1, tzinfo=IST),
        )
        other_result, other_profile = _build_debate_result(
            build_market_series(count=90)
        )
        conflicting = stored_debate_run(
            other_result,
            other_profile,
            stored_at=datetime(2026, 1, 1, tzinfo=IST),
            run_id=stored.run_id,
        )
        with self.storage() as storage:
            storage.save_debate_run(stored)
            with self.assertRaises(StorageConflictError):
                storage.save_debate_run(conflicting)
            self.assertTrue(storage.delete_debate_run(stored.run_id))
            self.assertFalse(storage.delete_debate_run(stored.run_id))
            self.assertIsNone(storage.get_debate_run(stored.run_id))

    def test_find_similar_debate_runs_via_sql_overlap(self):
        result, profile = _build_debate_result()
        high = stored_debate_run(
            result,
            profile,
            stored_at=datetime(2026, 1, 1, tzinfo=IST),
            run_id="debate-high",
        ).model_copy(
            update={"signature": ("trend:bullish", "momentum:bullish")}
        )
        low = high.model_copy(
            update={"run_id": "debate-low", "signature": ("trend:bullish",)}
        )
        none_matching = high.model_copy(
            update={
                "run_id": "debate-none",
                "signature": ("volume:bearish",),
            }
        )
        with self.storage() as storage:
            for stored in (high, low, none_matching):
                storage.save_debate_run(stored)

            results = storage.find_similar_debate_runs(
                ("trend:bullish", "momentum:bullish"),
            )

        self.assertEqual(
            [summary.run_id for summary in results],
            ["debate-high", "debate-low"],
        )

    def test_debate_run_query_filters_by_winner(self):
        result, profile = _build_debate_result()
        stored = stored_debate_run(
            result,
            profile,
            stored_at=datetime(2026, 1, 1, tzinfo=IST),
        )
        with self.storage() as storage:
            storage.save_debate_run(stored)

            matches = storage.list_debate_runs(
                DebateRunQuery(winner=SignalDirection.BULLISH)
            )
            no_matches = storage.list_debate_runs(
                DebateRunQuery(winner=SignalDirection.BEARISH)
            )

        self.assertEqual(len(matches), 1)
        self.assertEqual(no_matches, ())

    def test_closed_adapter_rejects_operations_and_close_is_idempotent(self):
        storage = self.storage()
        storage.close()
        storage.close()

        with self.assertRaisesRegex(StorageError, "closed"):
            storage.list_market_series()

    def test_wraps_invalid_database_location(self):
        missing_parent = Path(self.temporary_directory.name) / "missing" / "db"

        with self.assertRaisesRegex(StorageError, "initialize DuckDB"):
            DuckDBJarvisStorage(missing_parent)

    def test_rejects_blank_database_name(self):
        with self.assertRaisesRegex(ValueError, "cannot be blank"):
            DuckDBJarvisStorage("   ")


if __name__ == "__main__":
    unittest.main()
