from datetime import UTC, datetime, timedelta
import unittest

from app.fundamentals.session_provisioning import (
    ProviderSessionInspectionRequest,
    ProviderSessionLifecycle,
    ProviderSessionProvisioningRequest,
    ProviderSessionRevocationReason,
    ProviderSessionRevocationRequest,
    ProviderSessionStatus,
)
from app.models.fundamentals import ProviderConnectionScope
from app.services.provider_sessions import (
    ProviderSessionCommandError,
    ProviderSessionService,
)


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


def connection(**overrides) -> ProviderConnectionScope:
    values = {
        "tenant_id": "tenant.prateek",
        "provider_connection_id": "provider.tijori.prateek",
        "provider": "tijori",
        "account_reference_hash": "a" * 64,
    }
    values.update(overrides)
    return ProviderConnectionScope(**values)


class FakeProvisioner:
    configuration_fingerprint = "f" * 64

    def __init__(self) -> None:
        self.calls = []

    def inspect(self, *, request):
        self.calls.append(("status", request))
        return ProviderSessionLifecycle(
            connection=request.connection,
            status=ProviderSessionStatus.UNCONFIGURED,
            checked_at=NOW,
        )

    def provision(self, *, request):
        self.calls.append(("provision", request))
        return ready_lifecycle(request.connection)

    def revoke(self, *, request):
        self.calls.append(("revoke", request))
        return ProviderSessionLifecycle(
            connection=request.connection,
            status=ProviderSessionStatus.REVOKED,
            checked_at=NOW,
            session_reference_hash="b" * 64,
            provisioned_at=NOW - timedelta(hours=1),
            revoked_at=NOW,
            revocation_reason=request.reason,
        )


def ready_lifecycle(
    scope: ProviderConnectionScope,
) -> ProviderSessionLifecycle:
    return ProviderSessionLifecycle(
        connection=scope,
        status=ProviderSessionStatus.READY,
        checked_at=NOW,
        session_reference_hash="b" * 64,
        provisioned_at=NOW - timedelta(hours=1),
        expires_at=NOW + timedelta(hours=11),
    )


class ProviderSessionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provisioner = FakeProvisioner()
        self.service = ProviderSessionService(self.provisioner)
        self.scope = connection()

    def test_delegates_status_without_provider_specific_inputs(self):
        request = ProviderSessionInspectionRequest(
            request_id="session.status.1",
            connection=self.scope,
            requested_at=NOW,
        )

        lifecycle = self.service.status(request=request)

        self.assertEqual(lifecycle.status, ProviderSessionStatus.UNCONFIGURED)
        self.assertEqual(self.provisioner.calls, [("status", request)])
        self.assertEqual(self.service.configuration_fingerprint, "f" * 64)

    def test_delegates_explicitly_authorized_provisioning(self):
        request = ProviderSessionProvisioningRequest(
            request_id="session.provision.1",
            connection=self.scope,
            requested_at=NOW,
            user_interaction_authorized=True,
        )

        lifecycle = self.service.provision(request=request)

        self.assertEqual(lifecycle.status, ProviderSessionStatus.READY)
        self.assertEqual(self.provisioner.calls, [("provision", request)])

    def test_delegates_secure_revocation_with_reason(self):
        request = ProviderSessionRevocationRequest(
            request_id="session.revoke.1",
            connection=self.scope,
            requested_at=NOW,
            reason=ProviderSessionRevocationReason.USER_REQUESTED,
        )

        lifecycle = self.service.revoke(request=request)

        self.assertEqual(lifecycle.status, ProviderSessionStatus.REVOKED)
        self.assertEqual(
            lifecycle.revocation_reason,
            ProviderSessionRevocationReason.USER_REQUESTED,
        )
        self.assertEqual(self.provisioner.calls, [("revoke", request)])

    def test_rejects_wrong_command_request_before_adapter_call(self):
        request = ProviderSessionInspectionRequest(
            request_id="session.status.wrong-command",
            connection=self.scope,
            requested_at=NOW,
        )

        with self.assertRaises(TypeError):
            self.service.provision(request=request)

        self.assertEqual(self.provisioner.calls, [])

    def test_rejects_cross_scope_response_with_sanitized_error(self):
        request = ProviderSessionProvisioningRequest(
            request_id="session.provision.cross-scope",
            connection=self.scope,
            requested_at=NOW,
            user_interaction_authorized=True,
        )
        self.provisioner.provision = lambda *, request: ready_lifecycle(
            connection(tenant_id="tenant.other")
        )

        with self.assertRaises(ProviderSessionCommandError) as caught:
            self.service.provision(request=request)

        self.assertNotIn("tenant.other", str(caught.exception))

    def test_sanitizes_adapter_failure_without_releasing_details(self):
        request = ProviderSessionInspectionRequest(
            request_id="session.status.failure",
            connection=self.scope,
            requested_at=NOW,
        )

        def fail(*, request):
            raise RuntimeError("cookie=session-secret")

        self.provisioner.inspect = fail
        with self.assertRaises(ProviderSessionCommandError) as caught:
            self.service.status(request=request)

        self.assertNotIn("session-secret", str(caught.exception))

    def test_rejects_incomplete_provisioner_at_construction(self):
        with self.assertRaises(TypeError):
            ProviderSessionService(object())

        provisioner = FakeProvisioner()
        provisioner.configuration_fingerprint = "session-secret"
        with self.assertRaises(TypeError) as caught:
            ProviderSessionService(provisioner)
        self.assertNotIn("session-secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
