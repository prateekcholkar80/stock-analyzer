import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.composition.browser import compose_jarvis_browser_operations
from app.conversation.config import JarvisConversationConfig
from app.workflow.browser_runner import (
    BrowserFundamentalEvidenceExecutor,
    BrowserFundamentalFilingEvidenceExecutor,
)


class _Handler:
    def execute(self, request, *, event_emitter, cancellation_token):
        raise AssertionError("composition must not execute an operation")


class _FundamentalExecutor:
    def resolve_issuer(self, request):
        raise AssertionError("composition must not resolve an issuer")

    def load(self, request, *, refresh_requested=False):
        raise AssertionError("composition must not retrieve evidence")

    def refresh_structured_document_package(self, request):
        raise AssertionError("composition must not refresh financial documents")

    def load_active_package(self, *, connection, issuer):
        raise AssertionError("composition must not load a package")


class _ExecutorWithoutPackageRefresh:
    def resolve_issuer(self, request, *, refresh_requested=False):
        raise AssertionError("composition must not resolve an issuer")

    def load(self, request, *, refresh_requested=False):
        raise AssertionError("composition must not retrieve evidence")


class _FundamentalAnalyst:
    def analyze(self, *, evidence, intelligence, as_of):
        raise AssertionError("composition must not invoke the analyst")


class _FilingEvidenceExecutor:
    def coordinate(self, request, *, refresh_requested=False):
        raise AssertionError("composition must not prepare filing evidence")


class BrowserCompositionTests(unittest.TestCase):
    def test_fundamental_executor_contract_requires_package_refresh(self):
        self.assertIsInstance(
            _FundamentalExecutor(),
            BrowserFundamentalEvidenceExecutor,
        )
        self.assertNotIsInstance(
            _ExecutorWithoutPackageRefresh(),
            BrowserFundamentalEvidenceExecutor,
        )

    def test_injects_optional_fundamental_dependencies_without_execution(self):
        executor = _FundamentalExecutor()
        filing_executor = _FilingEvidenceExecutor()
        analyst = _FundamentalAnalyst()
        resolver = lambda session_id: object()
        captured = {}

        def build_handler(*args, **kwargs):
            captured.update(kwargs)
            return _Handler()

        research = SimpleNamespace(
            judge_follow_up_executor=object(),
            ticker_resolution_executor=None,
        )
        with (
            patch(
                "app.composition.browser.compose_jarvis_swing_research",
                return_value=research,
            ),
            patch(
                "app.composition.browser.LazyJarvisResearchPresenter",
                return_value=object(),
            ),
            patch(
                "app.composition.browser.JarvisBrowserOperationHandler",
                side_effect=build_handler,
            ),
        ):
            application = compose_jarvis_browser_operations(
                object(),
                conversation_config=JarvisConversationConfig(
                    user_name="Prateek"
                ),
                fundamental_evidence_executor=executor,
                fundamental_filing_evidence_executor=filing_executor,
                provider_scope_resolver=resolver,
                fundamental_analyst=analyst,
                max_workers=1,
            )
        try:
            self.assertIs(captured["fundamental_evidence_executor"], executor)
            self.assertIs(
                captured["fundamental_filing_evidence_executor"],
                filing_executor,
            )
            self.assertIs(captured["provider_scope_resolver"], resolver)
            self.assertIs(captured["fundamental_analyst"], analyst)
            self.assertIsNone(
                captured["fundamental_sector_evidence_resolver"]
            )
        finally:
            application.runner.shutdown()

    def test_reuses_configured_nse_catalog_for_sector_evidence(self):
        captured = {}
        catalog = object()
        sector_resolver = object()
        research = SimpleNamespace(
            judge_follow_up_executor=object(),
            ticker_resolution_executor=None,
        )

        def build_handler(*args, **kwargs):
            captured.update(kwargs)
            return _Handler()

        with (
            patch(
                "app.composition.browser.compose_jarvis_swing_research",
                return_value=research,
            ),
            patch(
                "app.composition.browser.LazyJarvisResearchPresenter",
                return_value=object(),
            ),
            patch(
                "app.composition.browser.NseFundamentalSectorEvidenceResolver",
                return_value=sector_resolver,
            ) as build_sector_resolver,
            patch(
                "app.composition.browser.JarvisBrowserOperationHandler",
                side_effect=build_handler,
            ),
        ):
            application = compose_jarvis_browser_operations(
                object(),
                conversation_config=JarvisConversationConfig(
                    user_name="Prateek"
                ),
                nse_sector_catalog=catalog,
                max_workers=1,
            )
        try:
            build_sector_resolver.assert_called_once_with(catalog)
            self.assertIs(
                captured["fundamental_sector_evidence_resolver"],
                sector_resolver,
            )
        finally:
            application.runner.shutdown()

    def test_rejects_partial_fundamental_dependency_pair(self):
        with self.assertRaisesRegex(ValueError, "executor and resolver"):
            compose_jarvis_browser_operations(
                object(),
                conversation_config=JarvisConversationConfig(
                    user_name="Prateek"
                ),
                fundamental_evidence_executor=_FundamentalExecutor(),
            )

    def test_filing_executor_requires_fundamental_issuer_resolution(self):
        self.assertIsInstance(
            _FilingEvidenceExecutor(),
            BrowserFundamentalFilingEvidenceExecutor,
        )
        with self.assertRaisesRegex(ValueError, "issuer resolution"):
            compose_jarvis_browser_operations(
                object(),
                conversation_config=JarvisConversationConfig(
                    user_name="Prateek"
                ),
                fundamental_filing_evidence_executor=(
                    _FilingEvidenceExecutor()
                ),
            )


if __name__ == "__main__":
    unittest.main()
