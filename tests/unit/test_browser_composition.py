import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.composition.browser import compose_jarvis_browser_operations
from app.conversation.config import JarvisConversationConfig


class _Handler:
    def execute(self, request, *, event_emitter, cancellation_token):
        raise AssertionError("composition must not execute an operation")


class _FundamentalExecutor:
    def resolve_issuer(self, request):
        raise AssertionError("composition must not resolve an issuer")

    def load(self, request, *, refresh_requested=False):
        raise AssertionError("composition must not retrieve evidence")


class BrowserCompositionTests(unittest.TestCase):
    def test_injects_optional_fundamental_dependencies_without_execution(self):
        executor = _FundamentalExecutor()
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
                provider_scope_resolver=resolver,
                max_workers=1,
            )
        try:
            self.assertIs(captured["fundamental_evidence_executor"], executor)
            self.assertIs(captured["provider_scope_resolver"], resolver)
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


if __name__ == "__main__":
    unittest.main()
