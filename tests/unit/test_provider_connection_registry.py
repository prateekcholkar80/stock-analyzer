from types import SimpleNamespace
import unittest

from app.api.models import ProviderSessionTargetRequest
from app.fundamentals.provider_connections import (
    InMemoryProviderConnectionRegistry,
    ProviderConnectionResolutionError,
    ProviderConnectionScopeResolver,
)
from app.models.fundamentals import ProviderConnectionScope


def connection(
    *,
    tenant_id: str = "tenant.prateek",
    connection_id: str = "provider.tijori.primary",
    account_hash: str = "a" * 64,
) -> ProviderConnectionScope:
    return ProviderConnectionScope(
        tenant_id=tenant_id,
        provider_connection_id=connection_id,
        provider="tijori",
        account_reference_hash=account_hash,
    )


def target(
    *,
    connection_id: str = "provider.tijori.primary",
    account_hash: str = "a" * 64,
) -> ProviderSessionTargetRequest:
    return ProviderSessionTargetRequest(
        provider_connection_id=connection_id,
        provider="tijori",
        account_reference_hash=account_hash,
    )


class InMemoryProviderConnectionRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = InMemoryProviderConnectionRegistry()
        self.scope = connection()
        self.registry.register_connection(self.scope)
        self.registry.bind_browser_session(
            browser_session_id="session-prateek",
            tenant_id=self.scope.tenant_id,
        )

    def test_resolves_http_target_only_within_bound_tenant(self):
        resolved = self.registry("session-prateek", target())

        self.assertIsInstance(self.registry, ProviderConnectionScopeResolver)
        self.assertEqual(resolved, self.scope)

    def test_isolates_same_connection_identifier_between_tenants(self):
        other = connection(
            tenant_id="tenant.other",
            account_hash="b" * 64,
        )
        self.registry.register_connection(other)
        self.registry.bind_browser_session(
            browser_session_id="session-other",
            tenant_id=other.tenant_id,
        )

        resolved = self.registry(
            "session-other",
            target(account_hash="b" * 64),
        )

        self.assertEqual(resolved.tenant_id, "tenant.other")
        self.assertNotEqual(resolved, self.scope)

    def test_rejects_unbound_unknown_and_mismatched_targets_without_details(self):
        attempts = (
            ("session-unbound", target()),
            ("session-prateek", target(connection_id="provider.unknown")),
            ("session-prateek", target(account_hash="c" * 64)),
            (
                "session-prateek",
                ProviderSessionTargetRequest(
                    provider_connection_id="provider.tijori.primary",
                    provider="other",
                    account_reference_hash="a" * 64,
                ),
            ),
        )
        for session_id, requested_target in attempts:
            with self.subTest(session_id=session_id):
                with self.assertRaises(ProviderConnectionResolutionError) as caught:
                    self.registry(session_id, requested_target)
                message = str(caught.exception)
                self.assertNotIn(session_id, message)
                self.assertNotIn(requested_target.provider_connection_id, message)

    def test_registration_is_idempotent_but_rejects_conflicting_metadata(self):
        self.assertEqual(self.registry.register_connection(self.scope), self.scope)
        conflicting = self.scope.model_copy(
            update={"account_reference_hash": "d" * 64}
        )

        with self.assertRaises(ProviderConnectionResolutionError):
            self.registry.register_connection(conflicting)
        with self.assertRaises(ProviderConnectionResolutionError):
            self.registry.bind_browser_session(
                browser_session_id="session-prateek",
                tenant_id="tenant.other",
            )

    def test_unbind_and_remove_immediately_revoke_resolution(self):
        self.assertTrue(self.registry.unbind_browser_session("session-prateek"))
        with self.assertRaises(ProviderConnectionResolutionError):
            self.registry("session-prateek", target())

        self.registry.bind_browser_session(
            browser_session_id="session-prateek",
            tenant_id=self.scope.tenant_id,
        )
        self.assertTrue(
            self.registry.remove_connection(
                tenant_id=self.scope.tenant_id,
                provider_connection_id=self.scope.provider_connection_id,
            )
        )
        with self.assertRaises(ProviderConnectionResolutionError):
            self.registry("session-prateek", target())

    def test_rejects_malformed_runtime_inputs(self):
        class ExplodingTarget:
            provider_connection_id = "provider.tijori.primary"
            provider = "tijori"

            @property
            def account_reference_hash(self):
                raise RuntimeError("account-secret")

        malformed = (
            ("invalid session", target()),
            (
                "session-prateek",
                SimpleNamespace(
                    provider_connection_id="provider.tijori.primary",
                    provider="tijori",
                    account_reference_hash="not-a-hash",
                ),
            ),
            ("session-prateek", object()),
            ("session-prateek", ExplodingTarget()),
        )
        for session_id, requested_target in malformed:
            with self.subTest(session_id=session_id):
                with self.assertRaises(ProviderConnectionResolutionError) as caught:
                    self.registry(session_id, requested_target)
                self.assertNotIn("account-secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
