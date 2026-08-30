from datetime import UTC, datetime, timedelta
import unittest

from pydantic import ValidationError

from app.api.models import (
    ProviderSessionLifecycleResponse,
    ProviderSessionStatusRequest,
    ProvisionProviderSessionRequest,
    RevokeProviderSessionRequest,
)
from app.fundamentals.session_provisioning import (
    ProviderSessionLifecycle,
    ProviderSessionRevocationReason,
    ProviderSessionStatus,
)
from app.models.fundamentals import ProviderConnectionScope


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
TARGET = {
    "provider_connection_id": "provider.tijori.prateek",
    "provider": "tijori",
    "account_reference_hash": "a" * 64,
}


def connection() -> ProviderConnectionScope:
    return ProviderConnectionScope(
        tenant_id="tenant.prateek",
        **TARGET,
    )


class ProviderSessionApiModelTests(unittest.TestCase):
    def test_accepts_secret_free_status_provision_and_revoke_commands(self):
        status = ProviderSessionStatusRequest.model_validate({"target": TARGET})
        provision = ProvisionProviderSessionRequest.model_validate(
            {
                "idempotency_key": "session.provision.1",
                "target": TARGET,
                "user_interaction_authorized": True,
            }
        )
        revoke = RevokeProviderSessionRequest.model_validate(
            {
                "idempotency_key": "session.revoke.1",
                "target": TARGET,
                "reason": "user_requested",
            }
        )

        self.assertEqual(status.target.provider, "tijori")
        self.assertTrue(provision.user_interaction_authorized)
        self.assertFalse(provision.replace_existing)
        self.assertTrue(revoke.secure_delete_required)
        self.assertEqual(
            revoke.reason,
            ProviderSessionRevocationReason.USER_REQUESTED,
        )

    def test_rejects_credentials_unknown_fields_and_malformed_identity(self):
        invalid_targets = (
            {**TARGET, "password": "must-not-enter"},
            {**TARGET, "provider_connection_id": "invalid connection"},
            {**TARGET, "account_reference_hash": "not-a-hash"},
        )
        for target in invalid_targets:
            with self.subTest(target=tuple(target)):
                with self.assertRaises(ValidationError):
                    ProviderSessionStatusRequest.model_validate(
                        {"target": target}
                    )

    def test_requires_literal_boolean_authorization_and_secure_deletion(self):
        for authorization in (False, 1, "true", None):
            with self.subTest(authorization=authorization):
                with self.assertRaises(ValidationError):
                    ProvisionProviderSessionRequest.model_validate(
                        {
                            "idempotency_key": "session.provision.invalid",
                            "target": TARGET,
                            "user_interaction_authorized": authorization,
                        }
                    )
        for secure in (False, 1, "true", None):
            with self.subTest(secure=secure):
                with self.assertRaises(ValidationError):
                    RevokeProviderSessionRequest.model_validate(
                        {
                            "idempotency_key": "session.revoke.invalid",
                            "target": TARGET,
                            "reason": "user_requested",
                            "secure_delete_required": secure,
                        }
                    )

    def test_projects_lifecycle_without_sensitive_scope_or_session_reference(self):
        lifecycle = ProviderSessionLifecycle(
            connection=connection(),
            status=ProviderSessionStatus.READY,
            checked_at=NOW,
            session_reference_hash="b" * 64,
            provisioned_at=NOW - timedelta(hours=1),
            expires_at=NOW + timedelta(hours=11),
        )

        response = ProviderSessionLifecycleResponse.from_lifecycle(lifecycle)
        serialized = response.model_dump_json()

        self.assertEqual(response.status, ProviderSessionStatus.READY)
        self.assertNotIn("tenant.prateek", serialized)
        self.assertNotIn("account_reference_hash", serialized)
        self.assertNotIn("session_reference_hash", serialized)
        self.assertNotIn("b" * 64, serialized)

    def test_rejects_inconsistent_or_timezone_naive_response(self):
        base = {
            "provider_connection_id": "provider.tijori.prateek",
            "provider": "tijori",
            "status": ProviderSessionStatus.READY,
            "checked_at": NOW,
            "provisioned_at": NOW - timedelta(hours=1),
            "expires_at": NOW + timedelta(hours=11),
            "lifecycle_fingerprint": "c" * 64,
        }
        for update in (
            {"checked_at": datetime(2026, 8, 29, 12, 0)},
            {"expires_at": NOW - timedelta(minutes=1)},
            {
                "revoked_at": NOW,
                "revocation_reason": (
                    ProviderSessionRevocationReason.USER_REQUESTED
                ),
            },
        ):
            with self.subTest(update=tuple(update)):
                with self.assertRaises(ValidationError):
                    ProviderSessionLifecycleResponse(**(base | update))


if __name__ == "__main__":
    unittest.main()
