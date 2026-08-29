import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from datetime import UTC, datetime

from pydantic import ValidationError

from app.fundamentals.tijori_mcp_contracts import (
    TIJORI_APPROVED_TOOLS,
    TijoriMcpTransport,
    TijoriMcpTransportError,
    TijoriToolStatus,
    TijoriTransportFailureKind,
)
from app.fundamentals.adapters.tijori_mcp import TijoriMcpAdapter
from app.fundamentals.transports.stdio_mcp import (
    TijoriStdioMcpSettings,
    TijoriStdioMcpTransport,
)
from app.models.fundamentals import ProviderConnectionScope
from app.gateways.fundamentals import FundamentalCompanySearchRequest


NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "tijori_mcp_synthetic_server.py"
).resolve()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TijoriStdioMcpSettingsTests(unittest.TestCase):
    def test_rejects_relative_paths_bad_hashes_and_unbounded_arguments(self):
        valid = {
            "runtime_executable": Path(sys.executable).resolve(),
            "runtime_sha256": "a" * 64,
            "server_entrypoint": FIXTURE,
            "server_sha256": "b" * 64,
            "session_root": Path("/tmp/sessions"),
        }
        with self.assertRaises(ValidationError):
            TijoriStdioMcpSettings(
                **{**valid, "runtime_executable": Path("python")}
            )
        with self.assertRaises(ValidationError):
            TijoriStdioMcpSettings(**{**valid, "runtime_sha256": "secret"})
        with self.assertRaises(ValidationError):
            TijoriStdioMcpSettings(
                **{**valid, "server_arguments": ("bad\nargument",)}
            )

    def test_settings_are_frozen_and_fingerprinted_without_session_content(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = TijoriStdioMcpSettings(
                runtime_executable=Path(sys.executable).resolve(),
                runtime_sha256=file_hash(Path(sys.executable).resolve()),
                server_entrypoint=FIXTURE,
                server_sha256=file_hash(FIXTURE),
                session_root=Path(directory),
            )
            self.assertEqual(len(settings.configuration_fingerprint), 64)
            with self.assertRaises(ValidationError):
                settings.timeout_seconds = 2.0


class TijoriStdioMcpTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.session_root = Path(self.temporary.name)
        self.session_root.chmod(0o700)
        python = Path(sys.executable).resolve()
        self.runtime = self.session_root / "pinned-runtime-launcher"
        self.runtime.write_text(
            "#!/bin/sh\nexec " + shlex.quote(str(python)) + " \"$@\"\n",
            encoding="utf-8",
        )
        self.runtime.chmod(0o700)
        self.settings = TijoriStdioMcpSettings(
            runtime_executable=self.runtime,
            runtime_sha256=file_hash(self.runtime),
            runtime_arguments=("-I",),
            server_entrypoint=FIXTURE,
            server_sha256=file_hash(FIXTURE),
            session_root=self.session_root,
            timeout_seconds=1.5,
            terminate_grace_seconds=0.1,
        )
        self.connection = ProviderConnectionScope(
            tenant_id="tenant-1",
            provider_connection_id="tijori-connection-1",
            provider="tijori",
            account_reference_hash="c" * 64,
        )
        self.transport = TijoriStdioMcpTransport(
            settings=self.settings,
            clock=lambda: NOW,
        )
        self.write_session("normal")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_session(self, mode: str, permissions: int = 0o600) -> Path:
        path = self.transport.session_path_for(self.connection)
        path.write_text(json.dumps({"mode": mode}), encoding="utf-8")
        path.chmod(permissions)
        return path

    def assert_transport_failure(self, kind, callable_):
        with self.assertRaises(TijoriMcpTransportError) as caught:
            callable_()
        self.assertEqual(caught.exception.kind, kind)
        self.assertNotIn("session", str(caught.exception).lower())

    def test_satisfies_transport_protocol_and_has_stable_fingerprint(self):
        self.assertIsInstance(self.transport, TijoriMcpTransport)
        self.assertEqual(len(self.transport.configuration_fingerprint), 64)
        self.assertNotIn("tenant-1", self.transport.configuration_fingerprint)

    def test_inspects_exact_five_tools_after_mcp_handshake(self):
        inspection = self.transport.inspect(connection=self.connection)
        self.assertEqual(inspection.available_tools, TIJORI_APPROVED_TOOLS)
        self.assertTrue(inspection.authenticated)
        self.assertEqual(inspection.checked_at, NOW)
        self.assertEqual(
            inspection.provider_contract_version,
            "tijori.synthetic_contract.v1",
        )

    def test_inspection_preserves_unauthenticated_state(self):
        self.write_session("unauthenticated")
        inspection = self.transport.inspect(connection=self.connection)
        self.assertFalse(inspection.authenticated)

    def test_calls_approved_tool_and_parses_bounded_text_envelope(self):
        result = self.transport.call_tool(
            connection=self.connection,
            tool_name="search_company",
            arguments={"query": "TCS", "max_results": 5},
        )
        self.assertEqual(result.tool_name, "search_company")
        self.assertEqual(result.status, TijoriToolStatus.SUCCESS)
        self.assertEqual(result.payload, {"companies": []})

    def test_semantic_failure_remains_a_typed_tool_result(self):
        self.write_session("semantic_not_found")
        result = self.transport.call_tool(
            connection=self.connection,
            tool_name="search_company",
            arguments={"query": "UNKNOWN"},
        )
        self.assertEqual(result.status, TijoriToolStatus.NOT_FOUND)
        self.assertIsNone(result.payload)

    def test_rejects_unapproved_tool_before_process_launch(self):
        self.assert_transport_failure(
            TijoriTransportFailureKind.CONFIGURATION,
            lambda: self.transport.call_tool(
                connection=self.connection,
                tool_name="fetch_document",
                arguments={},
            ),
        )

    def test_rejects_provider_mismatch_and_missing_session(self):
        wrong = self.connection.model_copy(update={"provider": "angel_one"})
        self.assert_transport_failure(
            TijoriTransportFailureKind.CONFIGURATION,
            lambda: self.transport.inspect(connection=wrong),
        )
        self.transport.session_path_for(self.connection).unlink()
        self.assert_transport_failure(
            TijoriTransportFailureKind.AUTHENTICATION,
            lambda: self.transport.inspect(connection=self.connection),
        )

    def test_rejects_insecure_session_root_file_and_symlink(self):
        self.session_root.chmod(0o755)
        self.assert_transport_failure(
            TijoriTransportFailureKind.CONFIGURATION,
            lambda: self.transport.inspect(connection=self.connection),
        )
        self.session_root.chmod(0o700)

        session_path = self.write_session("normal", permissions=0o644)
        self.assert_transport_failure(
            TijoriTransportFailureKind.AUTHENTICATION,
            lambda: self.transport.inspect(connection=self.connection),
        )
        session_path.unlink()
        target = self.session_root / "target.json"
        target.write_text("{}", encoding="utf-8")
        target.chmod(0o600)
        session_path.symlink_to(target)
        self.assert_transport_failure(
            TijoriTransportFailureKind.AUTHENTICATION,
            lambda: self.transport.inspect(connection=self.connection),
        )

    def test_rejects_entrypoint_hash_change_and_group_writable_code(self):
        copied = self.session_root / "server.py"
        copied.write_bytes(FIXTURE.read_bytes())
        copied.chmod(0o600)
        settings = self.settings.model_copy(
            update={
                "server_entrypoint": copied,
                "server_sha256": file_hash(copied),
            }
        )
        transport = TijoriStdioMcpTransport(settings=settings)
        copied.write_text("# changed", encoding="utf-8")
        self.assert_transport_failure(
            TijoriTransportFailureKind.CONFIGURATION,
            lambda: transport.inspect(connection=self.connection),
        )
        copied.write_bytes(FIXTURE.read_bytes())
        copied.chmod(0o620)
        self.assert_transport_failure(
            TijoriTransportFailureKind.CONFIGURATION,
            lambda: transport.inspect(connection=self.connection),
        )

    def test_rejects_protocol_and_contract_drift(self):
        for mode in (
            "wrong_protocol",
            "wrong_contract",
            "malformed_json",
            "mismatched_id",
        ):
            with self.subTest(mode=mode):
                self.write_session(mode)
                self.assert_transport_failure(
                    TijoriTransportFailureKind.PROTOCOL,
                    lambda: self.transport.inspect(connection=self.connection),
                )

    def test_rejects_oversized_response_and_accepts_server_notification(self):
        self.write_session("oversized_message")
        self.assert_transport_failure(
            TijoriTransportFailureKind.PROTOCOL,
            lambda: self.transport.inspect(connection=self.connection),
        )
        self.write_session("notification_first")
        inspection = self.transport.inspect(connection=self.connection)
        self.assertEqual(inspection.available_tools, TIJORI_APPROVED_TOOLS)

    def test_rejects_unapproved_server_catalog(self):
        self.write_session("unapproved_tool")
        self.assert_transport_failure(
            TijoriTransportFailureKind.PROTOCOL,
            lambda: self.transport.inspect(connection=self.connection),
        )

    def test_maps_rpc_authentication_without_exposing_server_message(self):
        self.write_session("rpc_authentication")
        with self.assertRaises(TijoriMcpTransportError) as caught:
            self.transport.inspect(connection=self.connection)
        self.assertEqual(
            caught.exception.kind,
            TijoriTransportFailureKind.AUTHENTICATION,
        )
        self.assertNotIn("must-not-leak", str(caught.exception))

    def test_timeout_is_bounded_and_process_is_cleaned_up(self):
        self.write_session("hang")
        self.assert_transport_failure(
            TijoriTransportFailureKind.UNAVAILABLE,
            lambda: self.transport.inspect(connection=self.connection),
        )

    def test_rejects_tool_identity_and_is_error_inconsistency(self):
        for mode in ("wrong_tool", "tool_error_success_envelope"):
            with self.subTest(mode=mode):
                self.write_session(mode)
                self.assert_transport_failure(
                    TijoriTransportFailureKind.PROTOCOL,
                    lambda: self.transport.call_tool(
                        connection=self.connection,
                        tool_name="search_company",
                        arguments={"query": "TCS"},
                    ),
                )

    def test_does_not_inherit_parent_environment(self):
        previous = os.environ.get("TIJORI_PASSWORD")
        os.environ["TIJORI_PASSWORD"] = "parent-secret"
        try:
            result = self.transport.call_tool(
                connection=self.connection,
                tool_name="search_company",
                arguments={"query": "TCS"},
            )
        finally:
            if previous is None:
                os.environ.pop("TIJORI_PASSWORD", None)
            else:
                os.environ["TIJORI_PASSWORD"] = previous
        self.assertEqual(result.status, TijoriToolStatus.SUCCESS)

    def test_rejects_non_json_tool_arguments(self):
        self.assert_transport_failure(
            TijoriTransportFailureKind.PROTOCOL,
            lambda: self.transport.call_tool(
                connection=self.connection,
                tool_name="search_company",
                arguments={"query": object()},
            ),
        )

    def test_offline_transport_and_adapter_run_end_to_end(self):
        adapter = TijoriMcpAdapter(
            transport=self.transport,
            clock=lambda: NOW,
        )
        manifest = adapter.inspect_capabilities(connection=self.connection)
        self.assertEqual(len(manifest.capabilities), 5)
        response = adapter.search_companies(
            request=FundamentalCompanySearchRequest(
                request_id="synthetic-transport-search",
                connection=self.connection,
                requested_at=NOW,
                query="TCS",
                exchanges=("NSE",),
            )
        )
        self.assertEqual(response.candidates, ())
        self.assertEqual(response.connection, self.connection)

    def test_session_path_is_scoped_and_non_identifying(self):
        path = self.transport.session_path_for(self.connection)
        self.assertEqual(path.parent, self.session_root)
        self.assertNotIn("tenant-1", path.name)
        self.assertNotIn("tijori-connection-1", path.name)
        other = self.connection.model_copy(
            update={"provider_connection_id": "tijori-connection-2"}
        )
        self.assertNotEqual(path, self.transport.session_path_for(other))


if __name__ == "__main__":
    unittest.main()
