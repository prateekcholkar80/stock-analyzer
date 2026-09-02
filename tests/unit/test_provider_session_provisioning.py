from datetime import UTC, datetime, timedelta
import unittest

from pydantic import ValidationError

from app.fundamentals.session_provisioning import (
    ProviderSessionInspectionRequest,
    ProviderSessionLifecycle,
    ProviderSessionProvisioner,
    ProviderSessionProvisioningRequest,
    ProviderSessionRevocationReason,
    ProviderSessionRevocationRequest,
    ProviderSessionStatus,
    validate_provider_session_response_binding,
)
from app.models.fundamentals import ProviderConnectionScope


NOW = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)
SESSION_HASH = "a" * 64


def connection(**overrides) -> ProviderConnectionScope:
    values = {
        "tenant_id": "tenant.prateek",
        "provider_connection_id": "provider.tijori.prateek",
        "provider": "tijori",
        "account_reference_hash": "b" * 64,
    }
    values.update(overrides)
    return ProviderConnectionScope(**values)


def inspection_request(**overrides) -> ProviderSessionInspectionRequest:
    values = {
        "request_id": "session.inspect.1",
        "connection": connection(),
        "requested_at": NOW,
    }
    values.update(overrides)
    return ProviderSessionInspectionRequest(**values)


def ready(**overrides) -> ProviderSessionLifecycle:
    values = {
        "connection": connection(),
        "status": ProviderSessionStatus.READY,
        "checked_at": NOW,
        "session_reference_hash": SESSION_HASH,
        "provisioned_at": NOW - timedelta(minutes=5),
        "expires_at": NOW + timedelta(days=10),
    }
    values.update(overrides)
    return ProviderSessionLifecycle(**values)


class FakeProvisioner:
    configuration_fingerprint = "c" * 64

    def inspect(self, *, request):
        return ready(connection=request.connection, checked_at=request.requested_at)

    def provision(self, *, request):
        return ready(connection=request.connection, checked_at=request.requested_at)

    def revoke(self, *, request):
        return ready(
            connection=request.connection,
            status=ProviderSessionStatus.REVOKED,
            checked_at=request.requested_at,
            revoked_at=request.requested_at,
            revocation_reason=request.reason,
        )


class ProviderSessionLifecycleTests(unittest.TestCase):
    def test_ready_lifecycle_is_secret_free_frozen_and_fingerprinted(self):
        lifecycle = ready()

        self.assertRegex(lifecycle.lifecycle_fingerprint, r"^[a-f0-9]{64}$")
        serialized = lifecycle.model_dump_json()
        for forbidden in ("password", "cookie", "csrf", "session_file", "path"):
            self.assertNotIn(forbidden, serialized.lower())
        with self.assertRaises(ValidationError):
            lifecycle.status = ProviderSessionStatus.EXPIRED

    def test_models_unconfigured_expired_and_revoked_states(self):
        unconfigured = ProviderSessionLifecycle(
            connection=connection(),
            status=ProviderSessionStatus.UNCONFIGURED,
            checked_at=NOW,
        )
        expired = ready(
            status=ProviderSessionStatus.EXPIRED,
            expires_at=NOW - timedelta(seconds=1),
        )
        revoked = ready(
            status=ProviderSessionStatus.REVOKED,
            revoked_at=NOW - timedelta(seconds=1),
            revocation_reason=ProviderSessionRevocationReason.USER_REQUESTED,
        )

        self.assertEqual(unconfigured.status, ProviderSessionStatus.UNCONFIGURED)
        self.assertEqual(expired.status, ProviderSessionStatus.EXPIRED)
        self.assertEqual(revoked.status, ProviderSessionStatus.REVOKED)

    def test_rejects_inconsistent_status_shapes_and_timestamps(self):
        invalid = (
            {"status": ProviderSessionStatus.UNCONFIGURED},
            {"checked_at": NOW - timedelta(minutes=10)},
            {"expires_at": NOW, "status": ProviderSessionStatus.READY},
            {"status": ProviderSessionStatus.EXPIRED, "expires_at": None},
            {"status": ProviderSessionStatus.REVOKED, "revoked_at": None},
        )
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValidationError):
                    ready(**overrides)
        with self.assertRaises(ValidationError):
            ready(checked_at=datetime(2026, 8, 29, 10, 0))


class ProviderSessionRequestTests(unittest.TestCase):
    def test_provisioning_requires_explicit_user_authorization(self):
        values = {
            "request_id": "session.provision.1",
            "connection": connection(),
            "requested_at": NOW,
            "user_interaction_authorized": True,
        }
        request = ProviderSessionProvisioningRequest(**values)
        self.assertTrue(request.user_interaction_authorized)

        with self.assertRaises(ValidationError):
            ProviderSessionProvisioningRequest(
                **{**values, "user_interaction_authorized": False}
            )

    def test_revocation_requires_secure_deletion(self):
        values = {
            "request_id": "session.revoke.1",
            "connection": connection(),
            "requested_at": NOW,
            "reason": ProviderSessionRevocationReason.SECURITY_ROTATION,
        }
        request = ProviderSessionRevocationRequest(**values)
        self.assertTrue(request.secure_delete_required)

        with self.assertRaises(ValidationError):
            ProviderSessionRevocationRequest(
                **{**values, "secure_delete_required": False}
            )

    def test_rejects_credential_fields_without_echoing_values(self):
        secret = "must-not-escape"
        with self.assertRaises(ValidationError) as caught:
            ProviderSessionProvisioningRequest(
                request_id="session.provision.secret",
                connection=connection(),
                requested_at=NOW,
                user_interaction_authorized=True,
                password=secret,
            )
        self.assertNotIn(secret, str(caught.exception))


class ProviderSessionBindingTests(unittest.TestCase):
    def test_binds_inspection_provisioning_and_revocation_to_connection(self):
        inspect = inspection_request()
        validate_provider_session_response_binding(inspect, ready())

        provision = ProviderSessionProvisioningRequest(
            request_id="session.provision.bind",
            connection=connection(),
            requested_at=NOW,
            user_interaction_authorized=True,
        )
        validate_provider_session_response_binding(provision, ready())

        revoke = ProviderSessionRevocationRequest(
            request_id="session.revoke.bind",
            connection=connection(),
            requested_at=NOW,
            reason=ProviderSessionRevocationReason.USER_REQUESTED,
        )
        validate_provider_session_response_binding(
            revoke,
            ready(
                status=ProviderSessionStatus.REVOKED,
                revoked_at=NOW,
                revocation_reason=revoke.reason,
            ),
        )

    def test_rejects_cross_scope_stale_and_wrong_operation_states(self):
        inspect = inspection_request()
        with self.assertRaisesRegex(ValueError, "crossed"):
            validate_provider_session_response_binding(
                inspect,
                ready(connection=connection(tenant_id="tenant.other")),
            )
        with self.assertRaisesRegex(ValueError, "predates"):
            validate_provider_session_response_binding(
                inspect,
                ready(checked_at=NOW - timedelta(seconds=1)),
            )

        provision = ProviderSessionProvisioningRequest(
            request_id="session.provision.wrong",
            connection=connection(),
            requested_at=NOW,
            user_interaction_authorized=True,
        )
        with self.assertRaisesRegex(ValueError, "ready"):
            validate_provider_session_response_binding(
                provision,
                ProviderSessionLifecycle(
                    connection=connection(),
                    status=ProviderSessionStatus.UNCONFIGURED,
                    checked_at=NOW,
                ),
            )

    def test_runtime_protocol_accepts_a_credential_free_implementation(self):
        provisioner = FakeProvisioner()
        self.assertIsInstance(provisioner, ProviderSessionProvisioner)
        self.assertEqual(len(provisioner.configuration_fingerprint), 64)


if __name__ == "__main__":
    unittest.main()
