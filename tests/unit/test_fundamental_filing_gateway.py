import unittest
from datetime import timedelta

from pydantic import ValidationError

from app.gateways.fundamentals import (
    FundamentalCapability,
    FundamentalCapabilityDescriptor,
    FundamentalCapabilityStatus,
    FundamentalFilingAcquisitionRequest,
    FundamentalFilingAcquisitionResult,
    FundamentalFilingDiscoveryRequest,
    FundamentalFilingDiscoveryResult,
    FundamentalFilingGateway,
    FundamentalRetrievalStatus,
    validate_fundamental_response_binding,
)
from app.models.fundamental_filings import (
    FundamentalFilingCategory,
    FundamentalFilingSourceClass,
)
from tests.unit.test_fundamental_filings import (
    COMMITTED_AT,
    DISCOVERED_AT,
    REQUESTED_AT,
    artifact,
    discovery,
    reference,
)
from tests.unit.test_fundamental_gateway import build_connection, build_issuer


ADAPTER_FINGERPRINT = "b" * 64


def _response_values(request, **updates):
    values = {
        "request_id": request.request_id,
        "request_fingerprint": request.request_fingerprint,
        "connection": request.connection,
        "requested_at": request.requested_at,
        "started_at": request.requested_at,
        "completed_at": COMMITTED_AT,
        "provider_contract_version": "provider.contract.v1",
        "adapter_fingerprint": ADAPTER_FINGERPRINT,
    }
    values.update(updates)
    return values


def _discovery_request(**updates):
    snapshot = discovery()
    values = {
        "request_id": "request.filing.discovery.coforge",
        "operation_id": "operation.filing.refresh.coforge",
        "connection": snapshot.connection,
        "requested_at": REQUESTED_AT,
        "issuer": snapshot.issuer,
        "categories": snapshot.categories,
        "from_fiscal_year": snapshot.from_fiscal_year,
        "through_date": snapshot.through_date,
        "max_documents": snapshot.max_documents,
    }
    values.update(updates)
    return FundamentalFilingDiscoveryRequest(**values)


def _acquisition_request(**updates):
    snapshot = discovery()
    values = {
        "request_id": "request.filing.acquire.coforge.fy2027.q1",
        "operation_id": "operation.filing.refresh.coforge",
        "connection": snapshot.connection,
        "requested_at": DISCOVERED_AT,
        "issuer": snapshot.issuer,
        "reference": snapshot.references[0],
        "discovery_fingerprint": snapshot.discovery_fingerprint,
    }
    values.update(updates)
    return FundamentalFilingAcquisitionRequest(**values)


class FundamentalFilingCapabilityTests(unittest.TestCase):
    def test_marks_discovery_read_only_and_acquisition_non_read_only(self):
        discovery_capability = FundamentalCapabilityDescriptor(
            capability=FundamentalCapability.FILING_DISCOVERY,
            status=FundamentalCapabilityStatus.AVAILABLE,
            supports_history=True,
            max_periods=120,
        )
        acquisition_capability = FundamentalCapabilityDescriptor(
            capability=FundamentalCapability.FILING_ACQUISITION,
            status=FundamentalCapabilityStatus.AVAILABLE,
            read_only=False,
            supports_history=False,
        )

        self.assertTrue(discovery_capability.read_only)
        self.assertFalse(acquisition_capability.read_only)

        with self.assertRaisesRegex(ValidationError, "only filing acquisition"):
            FundamentalCapabilityDescriptor(
                capability=FundamentalCapability.FILING_ACQUISITION,
                status=FundamentalCapabilityStatus.AVAILABLE,
                supports_history=False,
            )
        with self.assertRaisesRegex(ValidationError, "only filing acquisition"):
            FundamentalCapabilityDescriptor(
                capability=FundamentalCapability.COMPANY_SEARCH,
                status=FundamentalCapabilityStatus.AVAILABLE,
                read_only=False,
                supports_history=False,
            )


class FundamentalFilingRequestTests(unittest.TestCase):
    def test_builds_bounded_discovery_and_exact_acquisition_requests(self):
        discovery_request = _discovery_request()
        acquisition_request = _acquisition_request()

        self.assertEqual(
            discovery_request.capability,
            FundamentalCapability.FILING_DISCOVERY,
        )
        self.assertEqual(
            acquisition_request.capability,
            FundamentalCapability.FILING_ACQUISITION,
        )
        self.assertEqual(
            acquisition_request.reference,
            discovery().references[0],
        )
        self.assertEqual(len(discovery_request.request_fingerprint), 64)

    def test_rejects_unsafe_discovery_and_acquisition_scope(self):
        with self.assertRaisesRegex(ValidationError, "categories must be unique"):
            _discovery_request(
                categories=(
                    FundamentalFilingCategory.EARNINGS_RELEASE,
                    FundamentalFilingCategory.EARNINGS_RELEASE,
                )
            )
        with self.assertRaisesRegex(ValidationError, "future date"):
            _discovery_request(
                through_date=REQUESTED_AT.date() + timedelta(days=1)
            )
        with self.assertRaisesRegex(ValidationError, "connection does not match"):
            _acquisition_request(
                connection=build_connection(tenant_id="tenant.other")
            )
        with self.assertRaisesRegex(ValidationError, "issuer does not match"):
            _acquisition_request(issuer=build_issuer(symbol="TCS-EQ"))
        with self.assertRaisesRegex(ValidationError, "predate discovery"):
            _acquisition_request(requested_at=REQUESTED_AT)


class FundamentalFilingResultTests(unittest.TestCase):
    def test_releases_validated_discovery_and_binds_exact_request_scope(self):
        request = _discovery_request()
        snapshot = discovery()
        result = FundamentalFilingDiscoveryResult(
            **_response_values(request),
            issuer=request.issuer,
            status=FundamentalRetrievalStatus.COMPLETED,
            discovery=snapshot,
        )

        validate_fundamental_response_binding(request, result)
        self.assertEqual(result.discovery, snapshot)
        self.assertEqual(len(result.result_fingerprint), 64)

        drifted_request = request.model_copy(update={"max_documents": 99})
        drifted_result = result.model_copy(
            update={
                "request_fingerprint": drifted_request.request_fingerprint,
            }
        )
        with self.assertRaisesRegex(ValueError, "scope does not match"):
            validate_fundamental_response_binding(
                drifted_request,
                drifted_result,
            )

    def test_releases_only_the_exact_acquired_artifact(self):
        request = _acquisition_request()
        manifest = artifact()
        result = FundamentalFilingAcquisitionResult(
            **_response_values(request),
            issuer=request.issuer,
            status=FundamentalRetrievalStatus.COMPLETED,
            artifact=manifest,
        )

        validate_fundamental_response_binding(request, result)
        self.assertEqual(result.artifact, manifest)

        different_reference = reference(
            filing_reference_id="filing.coforge.fy2027.q1.call",
            category=FundamentalFilingCategory.CONFERENCE_CALL,
            source_class=FundamentalFilingSourceClass.EARNINGS_CALL_TRANSCRIPT,
            title="Coforge Q1 FY2027 Conference Call Summary",
            provider_label="CC-Jun26.pdf",
            original_filename="CC-Jun26.pdf",
            artifact_url=(
                "https://files.tijorifinance.com/insight/india/242/CC-Jun26.pdf"
            ),
        )
        drifted_request = request.model_copy(
            update={"reference": different_reference}
        )
        drifted_result = result.model_copy(
            update={
                "request_fingerprint": drifted_request.request_fingerprint,
            }
        )
        with self.assertRaisesRegex(ValueError, "artifact does not match"):
            validate_fundamental_response_binding(
                drifted_request,
                drifted_result,
            )

    def test_fails_closed_without_partial_discovery_or_artifact_release(self):
        discovery_request = _discovery_request()
        acquisition_request = _acquisition_request()

        for result_type, request, payload_name in (
            (
                FundamentalFilingDiscoveryResult,
                discovery_request,
                "discovery",
            ),
            (
                FundamentalFilingAcquisitionResult,
                acquisition_request,
                "artifact",
            ),
        ):
            with self.subTest(result_type=result_type.__name__):
                failed = result_type(
                    **_response_values(request),
                    issuer=request.issuer,
                    status=FundamentalRetrievalStatus.PROVIDER_UNAVAILABLE,
                    limitations=("Provider filing service is unavailable.",),
                )
                validate_fundamental_response_binding(request, failed)

                with self.assertRaisesRegex(
                    ValidationError,
                    "requires an explanation",
                ):
                    result_type(
                        **_response_values(request),
                        issuer=request.issuer,
                        status=(
                            FundamentalRetrievalStatus.PROVIDER_UNAVAILABLE
                        ),
                    )

                payload = (
                    discovery() if payload_name == "discovery" else artifact()
                )
                with self.assertRaisesRegex(ValidationError, "cannot release"):
                    result_type(
                        **_response_values(request),
                        issuer=request.issuer,
                        status=FundamentalRetrievalStatus.PARTIAL,
                        limitations=("One item failed validation.",),
                        **{payload_name: payload},
                    )


class _FilingGatewayStub:
    @property
    def configuration_fingerprint(self) -> str:
        return ADAPTER_FINGERPRINT

    def discover_filings(self, *, request):
        return FundamentalFilingDiscoveryResult(
            **_response_values(request),
            issuer=request.issuer,
            status=FundamentalRetrievalStatus.COMPLETED,
            discovery=discovery(),
        )

    def acquire_filing(self, *, request):
        return FundamentalFilingAcquisitionResult(
            **_response_values(request),
            issuer=request.issuer,
            status=FundamentalRetrievalStatus.COMPLETED,
            artifact=artifact(),
        )


class FundamentalFilingGatewayProtocolTests(unittest.TestCase):
    def test_accepts_adapter_implementing_both_neutral_capabilities(self):
        gateway = _FilingGatewayStub()

        self.assertIsInstance(gateway, FundamentalFilingGateway)
        self.assertEqual(
            gateway.discover_filings(request=_discovery_request()).status,
            FundamentalRetrievalStatus.COMPLETED,
        )
        self.assertEqual(
            gateway.acquire_filing(request=_acquisition_request()).status,
            FundamentalRetrievalStatus.COMPLETED,
        )


if __name__ == "__main__":
    unittest.main()
