from datetime import UTC, datetime, timedelta
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from app.fundamentals.local_session_provisioner import (
    InteractiveSessionProvisioningRequired,
    LocalFileProviderSessionProvisioner,
    LocalSessionProvisioningError,
)
from app.fundamentals.session_provisioning import (
    ProviderSessionInspectionRequest,
    ProviderSessionProvisioner,
    ProviderSessionProvisioningRequest,
    ProviderSessionRevocationReason,
    ProviderSessionRevocationRequest,
    ProviderSessionStatus,
)
from app.models.fundamentals import ProviderConnectionScope


NOW = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)
CONTENT = b'{"cookies":[],"origins":[]}'
REPLACEMENT_CONTENT = b'{"cookies":[{"name":"new"}],"origins":[]}'
INTERACTIVE_COMMAND = (Path("/usr/bin/node"), Path("/opt/jarvis/cli.js"))


def connection(**overrides) -> ProviderConnectionScope:
    values = {
        "tenant_id": "tenant.prateek",
        "provider_connection_id": "provider.tijori.prateek",
        "provider": "tijori",
        "account_reference_hash": "b" * 64,
    }
    values.update(overrides)
    return ProviderConnectionScope(**values)


class LocalFileProviderSessionProvisionerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.root.chmod(0o700)
        self.path = self.root / f"{'a' * 64}.json"
        self.provisioned_at = NOW - timedelta(hours=1)
        self.provisioner = self.build()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def build(self, **overrides):
        values = {
            "session_root": self.root,
            "session_path_resolver": lambda scoped: self.path,
            "session_max_age": timedelta(hours=12),
            "clock": lambda: NOW,
        }
        values.update(overrides)
        return LocalFileProviderSessionProvisioner(**values)

    def write_session(self, content=CONTENT, mode=0o600):
        self.path.write_bytes(content)
        self.path.chmod(mode)
        timestamp = self.provisioned_at.timestamp()
        os.utime(self.path, (timestamp, timestamp))

    def inspect_request(self, **overrides):
        values = {
            "request_id": "session.inspect.local",
            "connection": connection(),
            "requested_at": NOW - timedelta(seconds=1),
        }
        values.update(overrides)
        return ProviderSessionInspectionRequest(**values)

    def revoke_request(self, **overrides):
        values = {
            "request_id": "session.revoke.local",
            "connection": connection(),
            "requested_at": NOW - timedelta(seconds=1),
            "reason": ProviderSessionRevocationReason.USER_REQUESTED,
        }
        values.update(overrides)
        return ProviderSessionRevocationRequest(**values)

    def provision_request(self, **overrides):
        values = {
            "request_id": "session.provision.local",
            "connection": connection(),
            "requested_at": NOW - timedelta(seconds=1),
            "user_interaction_authorized": True,
        }
        values.update(overrides)
        return ProviderSessionProvisioningRequest(**values)

    def test_inspects_missing_session_as_unconfigured(self):
        lifecycle = self.provisioner.inspect(request=self.inspect_request())

        self.assertEqual(lifecycle.status, ProviderSessionStatus.UNCONFIGURED)
        self.assertIsNone(lifecycle.session_reference_hash)

    def test_inspects_owner_only_json_as_ready_without_releasing_content(self):
        self.write_session()
        lifecycle = self.provisioner.inspect(request=self.inspect_request())

        self.assertEqual(lifecycle.status, ProviderSessionStatus.READY)
        self.assertEqual(
            lifecycle.session_reference_hash,
            hashlib.sha256(CONTENT).hexdigest(),
        )
        self.assertEqual(lifecycle.provisioned_at, self.provisioned_at)
        serialized = lifecycle.model_dump_json()
        self.assertNotIn("cookies", serialized)
        self.assertNotIn(str(self.path), serialized)

    def test_classifies_session_expiry_from_explicit_local_policy(self):
        self.provisioned_at = NOW - timedelta(hours=13)
        self.write_session()

        lifecycle = self.provisioner.inspect(request=self.inspect_request())

        self.assertEqual(lifecycle.status, ProviderSessionStatus.EXPIRED)
        self.assertLessEqual(lifecycle.expires_at, lifecycle.checked_at)

    def test_rejects_insecure_root_file_symlink_and_hard_link(self):
        self.root.chmod(0o755)
        with self.assertRaises(LocalSessionProvisioningError):
            self.provisioner.inspect(request=self.inspect_request())
        self.root.chmod(0o700)

        self.write_session(mode=0o644)
        with self.assertRaises(LocalSessionProvisioningError):
            self.provisioner.inspect(request=self.inspect_request())
        self.path.unlink()

        target = self.root / "target.json"
        target.write_bytes(CONTENT)
        target.chmod(0o600)
        self.path.symlink_to(target)
        with self.assertRaises(LocalSessionProvisioningError):
            self.provisioner.inspect(request=self.inspect_request())
        self.path.unlink()

        self.write_session()
        linked = self.root / "linked.json"
        os.link(self.path, linked)
        with self.assertRaises(LocalSessionProvisioningError):
            self.provisioner.inspect(request=self.inspect_request())

    def test_rejects_malformed_oversized_and_non_object_json(self):
        for content, ceiling in (
            (b"not-json", 5_000_000),
            (b"[]", 5_000_000),
            (CONTENT, len(CONTENT) - 1),
        ):
            with self.subTest(content=content[:8], ceiling=ceiling):
                if self.path.exists():
                    self.path.unlink()
                self.write_session(content=content)
                provisioner = self.build(max_session_bytes=ceiling)
                with self.assertRaises(LocalSessionProvisioningError):
                    provisioner.inspect(request=self.inspect_request())

    def test_rejects_path_escape_and_noncanonical_filename(self):
        for candidate in (
            self.root.parent / f"{'a' * 64}.json",
            self.root / "tijori-session.json",
            Path("relative-session.json"),
        ):
            with self.subTest(candidate=candidate):
                provisioner = self.build(
                    session_path_resolver=lambda scoped, value=candidate: value
                )
                with self.assertRaises(LocalSessionProvisioningError):
                    provisioner.inspect(request=self.inspect_request())

    def test_revocation_overwrites_unlinks_and_returns_secret_free_metadata(self):
        self.write_session()
        lifecycle = self.provisioner.revoke(request=self.revoke_request())

        self.assertEqual(lifecycle.status, ProviderSessionStatus.REVOKED)
        self.assertEqual(
            lifecycle.revocation_reason,
            ProviderSessionRevocationReason.USER_REQUESTED,
        )
        self.assertFalse(self.path.exists())
        self.assertNotIn(str(self.path), lifecycle.model_dump_json())

    def test_missing_revocation_fails_safely_and_provisioning_is_separate(self):
        with self.assertRaises(LocalSessionProvisioningError) as caught:
            self.provisioner.revoke(request=self.revoke_request())
        self.assertNotIn(str(self.path), str(caught.exception))

        with self.assertRaises(InteractiveSessionProvisioningRequired):
            self.provisioner.provision(request=self.provision_request())

    def test_provisions_through_secret_free_bounded_process_and_rechecks_file(self):
        calls = []

        def runner(command, **options):
            calls.append((command, options))
            target = Path(options["env"]["JARVIS_TIJORI_SESSION_FILE"])
            target.write_bytes(CONTENT)
            target.chmod(0o600)
            os.utime(target, (NOW.timestamp(), NOW.timestamp()))
            fingerprint = hashlib.sha256(CONTENT).hexdigest()
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    '{"session_reference_hash":"'
                    f'{fingerprint}","status":"ready"}}\n'
                ),
            )

        provisioner = self.build(
            interactive_command=INTERACTIVE_COMMAND,
            interactive_timeout=timedelta(seconds=45),
            process_runner=runner,
        )
        lifecycle = provisioner.provision(request=self.provision_request())

        self.assertEqual(lifecycle.status, ProviderSessionStatus.READY)
        self.assertEqual(
            lifecycle.session_reference_hash,
            hashlib.sha256(CONTENT).hexdigest(),
        )
        self.assertEqual(len(calls), 1)
        command, options = calls[0]
        self.assertEqual(command, ("/usr/bin/node", "/opt/jarvis/cli.js"))
        self.assertNotIn(str(self.path), command)
        self.assertEqual(
            options["env"]["JARVIS_TIJORI_SESSION_FILE"],
            str(self.path),
        )
        self.assertEqual(
            options["env"]["JARVIS_TIJORI_PROVISION_TIMEOUT_MS"],
            "45000",
        )
        self.assertNotIn("TIJORI_USERNAME", options["env"])
        self.assertIs(options["stdin"], subprocess.DEVNULL)
        self.assertIs(options["stderr"], subprocess.DEVNULL)
        self.assertFalse(options.get("shell", False))

    def test_provisioning_rejects_existing_target_and_never_runs_process(self):
        self.write_session()
        calls = []
        provisioner = self.build(
            interactive_command=INTERACTIVE_COMMAND,
            process_runner=lambda *args, **kwargs: calls.append((args, kwargs)),
        )

        with self.assertRaises(LocalSessionProvisioningError):
            provisioner.provision(request=self.provision_request())

        self.assertEqual(calls, [])

    def test_replaces_only_an_expired_session_after_successful_login(self):
        self.provisioned_at = NOW - timedelta(hours=13)
        self.write_session()
        calls = []

        def runner(command, **options):
            calls.append(command)
            target = Path(options["env"]["JARVIS_TIJORI_SESSION_FILE"])
            self.assertFalse(target.exists())
            target.write_bytes(REPLACEMENT_CONTENT)
            target.chmod(0o600)
            os.utime(target, (NOW.timestamp(), NOW.timestamp()))
            fingerprint = hashlib.sha256(REPLACEMENT_CONTENT).hexdigest()
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    '{"session_reference_hash":"'
                    f'{fingerprint}","status":"ready"}}\n'
                ),
            )

        provisioner = self.build(
            interactive_command=INTERACTIVE_COMMAND,
            process_runner=runner,
        )
        lifecycle = provisioner.provision(
            request=self.provision_request(replace_existing=True)
        )

        self.assertEqual(lifecycle.status, ProviderSessionStatus.READY)
        self.assertEqual(self.path.read_bytes(), REPLACEMENT_CONTENT)
        self.assertEqual(len(calls), 1)
        self.assertEqual(list(self.root.glob(".replacement-*.tmp")), [])

    def test_failed_replacement_restores_the_expired_session(self):
        self.provisioned_at = NOW - timedelta(hours=13)
        self.write_session()

        def runner(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="node", timeout=45)

        provisioner = self.build(
            interactive_command=INTERACTIVE_COMMAND,
            process_runner=runner,
        )
        with self.assertRaises(LocalSessionProvisioningError):
            provisioner.provision(
                request=self.provision_request(replace_existing=True)
            )

        self.assertEqual(self.path.read_bytes(), CONTENT)
        lifecycle = provisioner.inspect(request=self.inspect_request())
        self.assertEqual(lifecycle.status, ProviderSessionStatus.EXPIRED)
        self.assertEqual(list(self.root.glob(".replacement-*.tmp")), [])

    def test_tampered_replacement_is_removed_before_old_session_is_restored(self):
        self.provisioned_at = NOW - timedelta(hours=13)
        self.write_session()

        def runner(command, **options):
            target = Path(options["env"]["JARVIS_TIJORI_SESSION_FILE"])
            target.write_bytes(REPLACEMENT_CONTENT)
            target.chmod(0o600)
            os.utime(target, (NOW.timestamp(), NOW.timestamp()))
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    '{"session_reference_hash":"'
                    f'{"c" * 64}","status":"ready"}}\n'
                ),
            )

        provisioner = self.build(
            interactive_command=INTERACTIVE_COMMAND,
            process_runner=runner,
        )
        with self.assertRaises(LocalSessionProvisioningError):
            provisioner.provision(
                request=self.provision_request(replace_existing=True)
            )

        self.assertEqual(self.path.read_bytes(), CONTENT)
        self.assertEqual(list(self.root.glob(".replacement-*.tmp")), [])

    def test_ready_session_cannot_be_replaced(self):
        self.write_session()
        calls = []
        provisioner = self.build(
            interactive_command=INTERACTIVE_COMMAND,
            process_runner=lambda *args, **kwargs: calls.append((args, kwargs)),
        )

        with self.assertRaises(LocalSessionProvisioningError):
            provisioner.provision(
                request=self.provision_request(replace_existing=True)
            )

        self.assertEqual(self.path.read_bytes(), CONTENT)
        self.assertEqual(calls, [])

    def test_provisioning_fails_closed_for_process_and_response_tampering(self):
        valid_hash = hashlib.sha256(CONTENT).hexdigest()
        scenarios = (
            object(),
            subprocess.CompletedProcess([], 1, stdout="provider secret\n"),
            subprocess.CompletedProcess([], 0, stdout="not-json\n"),
            subprocess.CompletedProcess(
                [], 0, stdout='{"status":"ready","session_reference_hash":7}\n'
            ),
            subprocess.CompletedProcess(
                [],
                0,
                stdout=(
                    f'{{"status":"ready","session_reference_hash":'
                    f'"{valid_hash}","extra":1}}\n'
                ),
            ),
            subprocess.CompletedProcess(
                [],
                0,
                stdout=(
                    '{"status":"ready","session_reference_hash":"'
                    f'{"c" * 64}"}}\n'
                ),
            ),
        )
        for completed in scenarios:
            with self.subTest(stdout=str(getattr(completed, "stdout", None))[:16]):
                if self.path.exists():
                    self.path.unlink()

                def runner(*args, result=completed, **kwargs):
                    if (
                        getattr(result, "returncode", None) == 0
                        and isinstance(getattr(result, "stdout", None), str)
                        and result.stdout.startswith("{")
                    ):
                        self.write_session()
                    return result

                provisioner = self.build(
                    interactive_command=INTERACTIVE_COMMAND,
                    process_runner=runner,
                )
                with self.assertRaises(LocalSessionProvisioningError) as caught:
                    provisioner.provision(request=self.provision_request())
                self.assertNotIn("provider secret", str(caught.exception))

    def test_validates_interactive_process_configuration(self):
        with self.assertRaises(TypeError):
            self.build(interactive_command=(Path("node"), Path("cli.js")))
        with self.assertRaises(ValueError):
            self.build(interactive_timeout=timedelta(seconds=29))
        with self.assertRaises(ValueError):
            self.build(interactive_timeout=timedelta(minutes=16))
        with self.assertRaises(ValueError):
            self.build(process_runner=lambda: None)

    def test_validates_configuration_clock_and_runtime_protocol(self):
        self.assertIsInstance(self.provisioner, ProviderSessionProvisioner)
        self.assertRegex(
            self.provisioner.configuration_fingerprint,
            r"^[a-f0-9]{64}$",
        )
        with self.assertRaises(TypeError):
            self.build(session_root=Path("relative"))
        with self.assertRaises(ValueError):
            self.build(session_max_age=timedelta(days=31))
        with self.assertRaises(ValueError):
            self.build(max_session_bytes=1)
        with self.assertRaises(LocalSessionProvisioningError):
            self.build(clock=lambda: datetime(2026, 8, 29, 10, 0)).inspect(
                request=self.inspect_request()
            )


if __name__ == "__main__":
    unittest.main()
