import io
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from app.commands.swing_analysis import JarvisSwingAnalysisCommandHandler
from app.composition.research import compose_jarvis_swing_research
from app.exceptions import InstrumentNotFoundError, LLMConfigurationError
from app.facades.swing_research import JarvisSwingResearchFacade
from app.instruments.amfi_market_cap import AmfiMarketCapCatalog, AmfiMarketCapConfig
from app.instruments.in_memory import InMemoryInstrumentResolver
from app.instruments.nse_sector_master import (
    NseSectorMasterCatalog,
    NseSectorMasterConfig,
)
from app.llm.config import LLMSettings
from app.llm.gateway import StructuredGeneration
from app.logging_config import get_operation_id
from app.models.instruments import ResolvedInstrument
from app.models.interaction import (
    JarvisCommandStatus,
    JarvisSwingAnalysisResponse,
    SwingAnalysisCommand,
)
from app.models.llm import LLMFailureCode
from app.models.storage import EndToEndSwingAnalysisResult
from app.models.workflow import WorkflowEventState, WorkflowStage
from app.workflow.events import InMemoryWorkflowEventSink


def _command(to_date=None):
    return SwingAnalysisCommand(
        exchange="NSE",
        symbol_token="2885",
        symbol="RELIANCE-EQ",
        interval="ONE_HOUR",
        to_date=to_date,
    )


def _result():
    return EndToEndSwingAnalysisResult.model_construct(
        use_case_id="jarvis.run_end_to_end_swing_analysis.v1",
        market_dataset_id="market:test",
        fetch=object(),
        technical_result=object(),
        debate_result=object(),
    )


class RecordingRequestResolver:
    def __init__(self, result=None, failure=None):
        self.result = result
        self.failure = failure
        self.calls = []

    def execute(self, text, *, to_date=None):
        self.calls.append((text, to_date, get_operation_id()))
        if self.failure is not None:
            raise self.failure
        return self.result


class RecordingCommandHandler:
    def __init__(self, response=None):
        self.response = response
        self.calls = []

    def execute(
        self,
        command,
        *,
        operation_id=None,
        event_emitter=None,
        emit_terminal_event=True,
    ):
        self.calls.append(
            (
                command,
                operation_id,
                get_operation_id(),
                event_emitter,
                emit_terminal_event,
            )
        )
        if self.response is not None:
            return self.response
        return JarvisSwingAnalysisResponse.completed(
            operation_id=operation_id,
            result=_result(),
        )


class RecordingExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, **kwargs):
        self.calls.append((kwargs, get_operation_id()))
        return _result()


class JarvisSwingResearchFacadeTests(unittest.TestCase):
    def test_routes_text_to_command_with_one_operation_id(self):
        as_of = datetime(2026, 8, 21, 15, 30, tzinfo=UTC)
        resolver = RecordingRequestResolver(result=_command(as_of))
        handler = RecordingCommandHandler()
        facade = JarvisSwingResearchFacade(resolver, handler)

        response = facade.execute(
            "How is Reliance looking for a swing trade?",
            to_date=as_of,
        )

        self.assertEqual(response.status, JarvisCommandStatus.COMPLETED)
        resolver_operation = resolver.calls[0][2]
        handler_operation = handler.calls[0][1]
        active_handler_operation = handler.calls[0][2]
        self.assertEqual(resolver_operation, response.operation_id)
        self.assertEqual(handler_operation, response.operation_id)
        self.assertEqual(active_handler_operation, response.operation_id)
        self.assertIsNone(get_operation_id())

    def test_resolution_failure_propagates_and_skips_command(self):
        sink = InMemoryWorkflowEventSink()
        handler = RecordingCommandHandler()
        facade = JarvisSwingResearchFacade(
            RecordingRequestResolver(
                failure=InstrumentNotFoundError("not found")
            ),
            handler,
            sink,
        )

        with self.assertRaises(InstrumentNotFoundError):
            facade.execute("Analyze Unknown for a swing trade")

        self.assertEqual(handler.calls, [])
        self.assertEqual(
            [(event.stage, event.state) for event in sink.events],
            [
                (
                    WorkflowStage.REQUEST_RECEIVED,
                    WorkflowEventState.COMPLETED,
                ),
                (WorkflowStage.FAILED, WorkflowEventState.FAILED),
            ],
        )
        self.assertIsNone(get_operation_id())

    def test_emits_request_resolution_and_terminal_success(self):
        sink = InMemoryWorkflowEventSink()
        facade = JarvisSwingResearchFacade(
            RecordingRequestResolver(result=_command()),
            JarvisSwingAnalysisCommandHandler(
                lambda: RecordingExecutor()
            ),
            sink,
        )

        response = facade.execute("Analyze Reliance for a swing trade")

        self.assertEqual(
            [event.stage for event in sink.events],
            [
                WorkflowStage.REQUEST_RECEIVED,
                WorkflowStage.INSTRUMENT_RESOLVED,
                WorkflowStage.COMPLETED,
            ],
        )
        self.assertTrue(
            all(
                event.operation_id == response.operation_id
                for event in sink.events
            )
        )

    def test_external_lifecycle_uses_supplied_id_without_terminal_event(self):
        sink = InMemoryWorkflowEventSink()
        handler = RecordingCommandHandler()
        facade = JarvisSwingResearchFacade(
            RecordingRequestResolver(result=_command()),
            handler,
        )
        from app.workflow.events import WorkflowEventEmitter

        emitter = WorkflowEventEmitter("browser-operation-1", sink)
        response = facade.execute(
            "Analyze Reliance for a swing trade",
            operation_id="browser-operation-1",
            event_emitter=emitter,
            manage_terminal_events=False,
        )

        self.assertEqual(response.operation_id, "browser-operation-1")
        self.assertFalse(handler.calls[0][4])
        self.assertEqual(
            [event.stage for event in sink.events],
            [
                WorkflowStage.REQUEST_RECEIVED,
                WorkflowStage.INSTRUMENT_RESOLVED,
            ],
        )

    def test_rejects_invalid_dependencies_and_collaborator_results(self):
        with self.assertRaises(ValueError):
            JarvisSwingResearchFacade(object(), object())
        with self.assertRaisesRegex(ValueError, "invalid command"):
            JarvisSwingResearchFacade(
                RecordingRequestResolver(result=object()),
                RecordingCommandHandler(),
            ).execute("request")
        with self.assertRaisesRegex(ValueError, "invalid response"):
            JarvisSwingResearchFacade(
                RecordingRequestResolver(result=_command()),
                RecordingCommandHandler(response=object()),
            ).execute("request")

    def test_rejects_mismatched_response_operation_id(self):
        response = JarvisSwingAnalysisResponse.completed(
            operation_id="wrong-operation",
            result=_result(),
        )
        facade = JarvisSwingResearchFacade(
            RecordingRequestResolver(result=_command()),
            RecordingCommandHandler(response=response),
        )

        with self.assertRaisesRegex(ValueError, "does not match"):
            facade.execute("request")


class FakeAngelIdentitySource:
    def __init__(self, instruments):
        self.instruments = tuple(instruments)
        self.refresh_calls = 0
        self._by_symbol = {item.symbol: item for item in self.instruments}

    def list_instruments(self):
        return self.instruments

    def refresh(self):
        self.refresh_calls += 1
        return len(self.instruments)

    def resolve(self, query, *, exchange=None):
        match = self._by_symbol.get(query)
        if match is None:
            raise InstrumentNotFoundError(
                "No configured instrument matches the requested company"
            )
        return match


class FakeTickerResolverGateway:
    def __init__(self, draft_payloads):
        self.draft_payloads = list(draft_payloads)
        self.calls = []

    @property
    def configuration_fingerprint(self):
        return "a" * 64

    def generate(self, *, system, messages, response_model):
        self.calls.append((system, messages))
        payload = self.draft_payloads.pop(0)
        return StructuredGeneration[response_model](
            value=response_model(**payload),
            provider="fake-provider",
            model="fake-resolver-model",
            attempt_count=1,
        )


def _empty_amfi_catalog(tmp_path):
    frame = pd.DataFrame(
        {
            "Sr No": [1],
            "Company Name": ["Reliance Industries Limited"],
            "NSE Symbol": ["RELIANCE"],
            "Category": ["Large Cap"],
        }
    )
    buffer = io.BytesIO()
    frame.to_excel(buffer, index=False, engine="openpyxl")
    config = AmfiMarketCapConfig(cache_path=Path(tmp_path) / "amfi.json")
    catalog = AmfiMarketCapCatalog(
        config, downloader=lambda *a: buffer.getvalue()
    )
    catalog._entries = ()
    return catalog


def _empty_nse_catalog(tmp_path):
    payload = (
        b"SYMBOL,NAME OF COMPANY,SECTOR,INDUSTRY\n"
        b"RELIANCE,Reliance Industries Limited,Energy,Oil & Gas\n"
    )
    config = NseSectorMasterConfig(cache_path=Path(tmp_path) / "nse.json")
    catalog = NseSectorMasterCatalog(config, downloader=lambda *a: payload)
    catalog._mapping = {}
    return catalog


class JarvisSwingResearchCompositionTests(unittest.TestCase):
    def setUp(self):
        self.instrument_resolver = InMemoryInstrumentResolver(
            (
                ResolvedInstrument(
                    exchange="NSE",
                    symbol_token="2885",
                    symbol="RELIANCE-EQ",
                    display_name="RELIANCE",
                ),
            )
        )
        self.settings = LLMSettings.from_environment(
            {"JARVIS_LLM_MODEL": "provider/model"}
        )

    def test_composition_is_lazy_and_maps_preflight_failure(self):
        calls = []
        sink = InMemoryWorkflowEventSink()

        def failing_preflight(factory):
            calls.append(factory)
            raise LLMConfigurationError("secret-preflight-failure")

        facade = compose_jarvis_swing_research(
            object(),
            instrument_resolver=self.instrument_resolver,
            settings=self.settings,
            preflight_builder=failing_preflight,
            event_sink=sink,
        )

        self.assertEqual(calls, [])
        response = facade.execute(
            "How is Reliance looking for a swing trade?"
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(response.status, JarvisCommandStatus.LLM_FAILURE)
        self.assertEqual(response.failure.code, LLMFailureCode.CONFIGURATION)
        self.assertNotIn(
            "secret-preflight-failure",
            response.model_dump_json(),
        )
        self.assertEqual(
            [event.stage for event in sink.events],
            [
                WorkflowStage.REQUEST_RECEIVED,
                WorkflowStage.INSTRUMENT_RESOLVED,
                WorkflowStage.FAILED,
            ],
        )

    def test_failed_lazy_composition_can_be_retried(self):
        calls = []

        def failing_preflight(factory):
            calls.append(factory)
            raise LLMConfigurationError("not ready")

        facade = compose_jarvis_swing_research(
            object(),
            instrument_resolver=self.instrument_resolver,
            settings=self.settings,
            preflight_builder=failing_preflight,
        )

        facade.execute("Analyze Reliance for a swing trade")
        facade.execute("Analyze Reliance for a swing trade")

        self.assertEqual(len(calls), 2)

    def test_successful_composition_is_cached_across_requests(self):
        executor = RecordingExecutor()

        with patch(
            "app.composition.research."
            "compose_end_to_end_multi_timeframe_swing_analysis",
            return_value=executor,
        ) as compose_executor:
            facade = compose_jarvis_swing_research(
                object(),
                instrument_resolver=self.instrument_resolver,
                settings=self.settings,
            )
            first = facade.execute(
                "How is Reliance looking for a swing trade?"
            )
            second = facade.execute(
                "Analyze Reliance for a swing trade"
            )

        compose_executor.assert_called_once()
        self.assertEqual(len(executor.calls), 2)
        self.assertNotEqual(first.operation_id, second.operation_id)
        self.assertEqual(
            executor.calls[0][0]["symbol_token"],
            "2885",
        )
        self.assertEqual(executor.calls[0][1], first.operation_id)
        self.assertEqual(executor.calls[1][1], second.operation_id)

    def test_rejects_conflicting_instrument_dependencies(self):
        from app.instruments.angel_master import AngelInstrumentMasterConfig

        with self.assertRaisesRegex(ValueError, "either"):
            compose_jarvis_swing_research(
                object(),
                instrument_resolver=self.instrument_resolver,
                instrument_config=AngelInstrumentMasterConfig(),
            )

    def test_ticker_resolution_executor_is_none_without_catalogs(self):
        facade = compose_jarvis_swing_research(
            object(),
            instrument_resolver=self.instrument_resolver,
            settings=self.settings,
        )

        self.assertIsNone(facade.ticker_resolution_executor)

    def test_ticker_resolution_executor_is_lazily_composed_when_catalogs_given(
        self,
    ):
        def eager_gateway_builder(role_settings):
            raise AssertionError(
                "gateway must not be built until first ticker-resolution use"
            )

        with TemporaryDirectory() as tmp:
            facade = compose_jarvis_swing_research(
                object(),
                instrument_resolver=FakeAngelIdentitySource(
                    (
                        ResolvedInstrument(
                            exchange="NSE",
                            symbol_token="2885",
                            symbol="RELIANCE-EQ",
                            display_name="Reliance Industries Limited",
                        ),
                    )
                ),
                settings=self.settings,
                gateway_builder=eager_gateway_builder,
                amfi_catalog=_empty_amfi_catalog(tmp),
                nse_sector_catalog=_empty_nse_catalog(tmp),
            )

        self.assertIsNotNone(facade.ticker_resolution_executor)
        # Composition itself never invoked the gateway builder.

    def test_ticker_resolution_executor_resolves_via_llm_on_first_use(self):
        gateway = FakeTickerResolverGateway(
            [{"chosen_symbols": ("RELIANCE-EQ",), "is_ambiguous": False}]
        )
        with TemporaryDirectory() as tmp:
            facade = compose_jarvis_swing_research(
                object(),
                instrument_resolver=FakeAngelIdentitySource(
                    (
                        ResolvedInstrument(
                            exchange="NSE",
                            symbol_token="2885",
                            symbol="RELIANCE-EQ",
                            display_name="Reliance Industries Limited",
                        ),
                    )
                ),
                settings=self.settings,
                gateway_builder=lambda role_settings: gateway,
                amfi_catalog=_empty_amfi_catalog(tmp),
                nse_sector_catalog=_empty_nse_catalog(tmp),
            )

            result = facade.ticker_resolution_executor.attempt(
                "Analyze Rel for me"
            )

        self.assertEqual(result.outcome, "resolved_needs_confirmation")
        self.assertEqual(result.chosen_symbol, "RELIANCE-EQ")


if __name__ == "__main__":
    unittest.main()
