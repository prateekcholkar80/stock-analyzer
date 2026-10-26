import unittest
from datetime import timedelta
from hashlib import sha256

from pydantic import ValidationError

from app.fundamentals.filing_evidence_validator import (
    DeterministicFundamentalFilingEvidenceValidator,
)
from app.models.corporate_filings_analysis import (
    CorporateFilingsAnalysisKind,
    CorporateFilingsAnalysisPolicy,
)
from app.models.fundamental_filing_evidence import (
    FundamentalFilingEvidenceValidationResult,
    FundamentalFilingValidationCode,
    FundamentalFilingValidationFinding,
    FundamentalFilingValidationStatus,
)
from app.models.fundamental_filings import (
    FundamentalFilingCategory,
    FundamentalFilingPackage,
    FundamentalFilingSourceClass,
)
from app.models.fundamentals import FundamentalIssuerIdentity
from app.services.corporate_filings_analysis_assembly import (
    CorporateFilingsAnalysisAssemblyError,
    CorporateFilingsAnalysisAssemblyInput,
    CorporateFilingsAnalysisPolicyProvider,
    CorporateFilingsAnalysisRequestAssembler,
)
from app.storage.fundamental_filing_evidence_repositories import (
    StoredFundamentalFilingEvidence,
)
from app.storage.fundamental_filing_repositories import (
    FundamentalFilingRepositoryScope,
)
from tests.unit.test_fundamental_filing_evidence import extraction
from tests.unit.test_fundamental_filings import COMMITTED_AT, package, period


def digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class StubFilingRepository:
    adapter_name = "stub_filing_repository"

    def __init__(self, active_package):
        self.active_package = active_package
        self.active_reads = []

    def save_filing_artifact(self, artifact):
        raise AssertionError("assembler must not write artifacts")

    def find_filing_artifacts_by_content_hash(self, content_sha256, *, scope):
        raise AssertionError("assembler must not search artifact history")

    def get_filing_artifact(self, artifact_id, *, scope):
        raise AssertionError("assembler must use only the active package")

    def list_filing_artifact_versions(self, filing_reference_id, *, scope):
        raise AssertionError("assembler must not select from artifact history")

    def commit_filing_package(self, package, *, expected_active_package_id):
        raise AssertionError("assembler must not publish a package")

    def get_active_filing_package(self, *, scope):
        self.active_reads.append(scope)
        return self.active_package

    def get_filing_package(self, package_id, *, scope):
        raise AssertionError("assembler must use only the active package")

    def delete_filing_artifact(self, artifact_id, *, scope, authorization):
        raise AssertionError("assembler must not delete artifacts")

    def delete_filing_package(self, package_id, *, scope, authorization):
        raise AssertionError("assembler must not delete packages")


class StubEvidenceRepository:
    adapter_name = "stub_filing_evidence_repository"

    def __init__(self, records=()):
        self.records = tuple(records)
        self.reads = []

    def save_filing_evidence(self, record):
        raise AssertionError("assembler must not write filing evidence")

    def get_filing_evidence(self, record_id, *, scope, as_of):
        raise AssertionError("assembler must use the scoped artifact listing")

    def list_filing_evidence_for_artifact(self, artifact_id, *, scope, as_of):
        self.reads.append((artifact_id, scope, as_of))
        return tuple(
            item
            for item in self.records
            if item.artifact_id == artifact_id
            and item.scope == scope
            and item.stored_at <= as_of
        )

    def find_filing_evidence_by_policy(self, key, *, scope, as_of):
        raise AssertionError("assembler must inspect only the active artifact")


class StubPolicyProvider:
    def __init__(self, policy=None):
        self.policy = analysis_policy() if policy is None else policy
        self.calls = 0

    def build_analysis_policy(self):
        self.calls += 1
        return self.policy


def analysis_policy() -> CorporateFilingsAnalysisPolicy:
    return CorporateFilingsAnalysisPolicy(
        policy_id="jarvis.corporate_filings_analysis",
        policy_version="1.0.0",
        prompt_sha256=digest("prompt"),
        skill_id="jarvis.fundamental_framework.jhunjhunwala_style.v1",
        skill_version="1.0.0",
        skill_sha256=digest("skill"),
        model_configuration_fingerprint=digest("model"),
        max_input_tokens=20_000,
        max_output_tokens=4_000,
        max_model_calls=2,
    )


def releaseable_evidence(
    *,
    evidence_value=None,
    stored_at=None,
) -> StoredFundamentalFilingEvidence:
    evidence = extraction() if evidence_value is None else evidence_value
    validation = FundamentalFilingEvidenceValidationResult(
        policy_id="filing.validation.default",
        policy_version="1.0.0",
        extraction_fingerprint=evidence.extraction_fingerprint,
        releaseable=True,
        findings=tuple(
            FundamentalFilingValidationFinding(
                code=code,
                status=FundamentalFilingValidationStatus.PASS,
                message=f"Synthetic {code.value} validation passed.",
            )
            for code in sorted(FundamentalFilingValidationCode)
        ),
        evidence_page_spans=(),
    )
    return StoredFundamentalFilingEvidence.from_validated(
        evidence=evidence,
        validation=validation,
        stored_at=(
            COMMITTED_AT + timedelta(minutes=2)
            if stored_at is None
            else stored_at
        ),
    )


def complete_quarter_package_and_evidence():
    active = package()
    earnings_artifact = active.artifacts[0]
    call_hash = digest("conference-call-pdf")
    call_reference = earnings_artifact.reference.model_copy(
        update={
            "filing_reference_id": "filing.coforge.fy2027.q1.cc",
            "category": FundamentalFilingCategory.CONFERENCE_CALL,
            "source_class": (
                FundamentalFilingSourceClass.EARNINGS_CALL_TRANSCRIPT
            ),
            "title": "Coforge Q1 FY2027 Conference Call",
            "provider_label": "CC-Jun26.pdf",
            "original_filename": "CC-Jun26.pdf",
            "artifact_url": (
                "https://files.tijorifinance.com/insight/india/242/"
                "CC-Jun26.pdf"
            ),
        }
    )
    call_artifact = earnings_artifact.model_copy(
        update={
            "reference": call_reference,
            "content_sha256": call_hash,
            "safe_relative_path": (
                "tenant/provider/INE591G01017/FY2027/Q1/"
                f"conference-call/{call_hash}.pdf"
            ),
        }
    )
    discovery = active.discovery.model_copy(
        update={"references": (earnings_artifact.reference, call_reference)}
    )
    manifest = active.manifest.model_copy(
        update={
            "discovery_fingerprint": discovery.discovery_fingerprint,
            "artifact_ids": (
                earnings_artifact.artifact_id,
                call_artifact.artifact_id,
            ),
        }
    )
    combined_package = FundamentalFilingPackage(
        manifest=manifest,
        discovery=discovery,
        artifacts=(earnings_artifact, call_artifact),
    )
    call_citations = tuple(
        sorted(
            (
                item.model_copy(
                    update={
                        "artifact_id": call_artifact.artifact_id,
                        "content_sha256": call_artifact.content_sha256,
                    }
                )
                for item in extraction().citations
            ),
            key=lambda item: item.citation_id,
        )
    )
    call_evidence = extraction(
        artifact=call_artifact,
        citations=call_citations,
        observed_facts=(),
        adjusted_metrics=(),
        management_claims=(),
        guidance_commitments=(),
        risk_disclosures=(),
    )
    return (
        combined_package,
        releaseable_evidence(),
        releaseable_evidence(evidence_value=call_evidence),
    )


def assembly_input(**updates) -> CorporateFilingsAnalysisAssemblyInput:
    active = package()
    values = {
        "request_id": "filing-analysis.request.1",
        "operation_id": "operation.filing-analysis.1",
        "scope": FundamentalFilingRepositoryScope.from_package(active),
        "period": period(),
        "analysis_kind": CorporateFilingsAnalysisKind.EARNINGS_RELEASE,
        "requested_at": COMMITTED_AT + timedelta(minutes=3),
        "assembled_at": COMMITTED_AT + timedelta(minutes=4),
        "as_of": COMMITTED_AT + timedelta(minutes=5),
    }
    values.update(updates)
    return CorporateFilingsAnalysisAssemblyInput(**values)


def assembler(*, active_package=None, records=None, policy_provider=None):
    filing_repository = StubFilingRepository(
        package()
        if active_package is None
        else (None if active_package is False else active_package)
    )
    evidence_repository = StubEvidenceRepository(
        (releaseable_evidence(),) if records is None else records
    )
    resolved_policy_provider = policy_provider or StubPolicyProvider()
    return (
        CorporateFilingsAnalysisRequestAssembler(
            filing_repository=filing_repository,
            evidence_repository=evidence_repository,
            policy_provider=resolved_policy_provider,
        ),
        filing_repository,
        evidence_repository,
        resolved_policy_provider,
    )


class CorporateFilingsAnalysisAssemblyTests(unittest.TestCase):
    def test_assembles_active_releaseable_quarter_with_exact_lineage(self):
        service, filing_repository, evidence_repository, policy_provider = (
            assembler()
        )
        requested = assembly_input()

        result = service.assemble(requested)

        document = result.bundle.documents[0]
        record = releaseable_evidence()
        self.assertEqual(result.request_id, requested.request_id)
        self.assertEqual(result.operation_id, requested.operation_id)
        self.assertEqual(result.bundle.filing_package_id, package().package_id)
        self.assertEqual(result.bundle.period, requested.period)
        self.assertEqual(document.artifact_id, record.artifact_id)
        self.assertEqual(
            document.extraction_fingerprint,
            record.extraction_fingerprint,
        )
        self.assertEqual(
            document.validation_fingerprint,
            record.validation_fingerprint,
        )
        self.assertEqual(
            {item.citation_id for item in document.citations},
            {
                citation_id
                for chunk in document.chunks
                for citation_id in chunk.citation_ids
            },
        )
        self.assertEqual(len(filing_repository.active_reads), 1)
        self.assertEqual(len(evidence_repository.reads), 1)
        self.assertEqual(policy_provider.calls, 1)
        self.assertEqual(result.policy, policy_provider.policy)

    def test_preserves_speaker_identity_and_citation_binding(self):
        service, _, _, _ = assembler()

        document = service.assemble(assembly_input()).bundle.documents[0]

        speaker = document.speakers[0]
        self.assertEqual(speaker.speaker_turn_id, "turn.1")
        self.assertEqual(speaker.speaker_name, "Chief Executive Officer")
        cited_speaker_ids = {
            speaker_id
            for chunk in document.chunks
            for speaker_id in chunk.speaker_turn_ids
        }
        self.assertEqual(cited_speaker_ids, {"turn.1"})
        self.assertIn('"exact_excerpt"', document.chunks[0].text)

    def test_assembles_both_required_documents_for_combined_analysis(self):
        active, earnings, conference_call = (
            complete_quarter_package_and_evidence()
        )
        service, _, evidence_repository, _ = assembler(
            active_package=active,
            records=(conference_call, earnings),
        )

        result = service.assemble(
            assembly_input(
                scope=FundamentalFilingRepositoryScope.from_package(active),
                analysis_kind=CorporateFilingsAnalysisKind.QUARTERLY_COMBINED,
            )
        )

        self.assertEqual(
            tuple(item.category for item in result.bundle.documents),
            (
                FundamentalFilingCategory.CONFERENCE_CALL,
                FundamentalFilingCategory.EARNINGS_RELEASE,
            ),
        )
        self.assertEqual(len(evidence_repository.reads), 2)

    def test_chooses_latest_visible_releaseable_extraction(self):
        first = releaseable_evidence()
        newer_evidence = extraction(
            policy=first.evidence.policy.model_copy(
                update={"policy_version": "1.1.0"}
            ),
            extracted_at=first.evidence.extracted_at + timedelta(minutes=1),
        )
        newer = releaseable_evidence(
            evidence_value=newer_evidence,
            stored_at=first.stored_at + timedelta(minutes=2),
        )
        service, _, _, _ = assembler(records=(newer, first))

        document = service.assemble(
            assembly_input(as_of=newer.stored_at + timedelta(minutes=1))
        ).bundle.documents[0]

        self.assertEqual(
            document.extraction_fingerprint,
            newer.extraction_fingerprint,
        )

    def test_preserves_last_releaseable_when_newer_validation_failed(self):
        released = releaseable_evidence()
        evidence = extraction(
            extracted_at=released.evidence.extracted_at + timedelta(minutes=1)
        )
        wrong_issuer = FundamentalIssuerIdentity(
            exchange="NSE",
            symbol="OTHER-EQ",
            legal_name="Other Limited",
            isin="INE000A01001",
            provider_company_id="999",
            provider_slug="other-limited",
        )
        failed_validation = (
            DeterministicFundamentalFilingEvidenceValidator().validate(
                evidence,
                expected_issuer=wrong_issuer,
                expected_period=evidence.artifact.reference.period,
                expected_source_class=evidence.artifact.reference.source_class,
            )
        )
        failed = StoredFundamentalFilingEvidence.from_validated(
            evidence=evidence,
            validation=failed_validation,
            stored_at=released.stored_at + timedelta(minutes=2),
        )
        service, _, _, _ = assembler(records=(released, failed))

        document = service.assemble(
            assembly_input(as_of=failed.stored_at + timedelta(minutes=1))
        ).bundle.documents[0]

        self.assertEqual(
            document.validation_fingerprint,
            released.validation_fingerprint,
        )

    def test_fails_closed_without_active_package_or_releaseable_evidence(self):
        no_package, _, _, _ = assembler(active_package=False)
        with self.assertRaisesRegex(
            CorporateFilingsAnalysisAssemblyError,
            "No active filing package",
        ):
            no_package.assemble(assembly_input())

        no_evidence, _, _, _ = assembler(records=())
        with self.assertRaisesRegex(
            CorporateFilingsAnalysisAssemblyError,
            "No releaseable extracted evidence",
        ):
            no_evidence.assemble(assembly_input())

    def test_fails_closed_for_missing_required_category_or_quarter(self):
        service, _, evidence_repository, _ = assembler()
        with self.assertRaisesRegex(
            CorporateFilingsAnalysisAssemblyError,
            "conference_call",
        ):
            service.assemble(
                assembly_input(
                    analysis_kind=(
                        CorporateFilingsAnalysisKind.QUARTERLY_COMBINED
                    )
                )
            )
        self.assertEqual(evidence_repository.reads, [])

        service, _, evidence_repository, _ = assembler()
        with self.assertRaisesRegex(
            CorporateFilingsAnalysisAssemblyError,
            "earnings_release",
        ):
            service.assemble(
                assembly_input(
                    period=period(
                        fiscal_quarter=2,
                        source_label="Q2 FY2027",
                    )
                )
            )
        self.assertEqual(evidence_repository.reads, [])

    def test_rejects_package_that_did_not_exist_at_as_of(self):
        service, _, _, _ = assembler()
        with self.assertRaisesRegex(
            CorporateFilingsAnalysisAssemblyError,
            "was not available",
        ):
            service.assemble(
                assembly_input(
                    requested_at=COMMITTED_AT - timedelta(minutes=2),
                    assembled_at=COMMITTED_AT - timedelta(minutes=1),
                    as_of=COMMITTED_AT - timedelta(seconds=1),
                )
            )

    def test_rejects_invalid_repositories_before_any_read(self):
        with self.assertRaisesRegex(TypeError, "filing repository"):
            CorporateFilingsAnalysisRequestAssembler(
                filing_repository=object(),
                evidence_repository=StubEvidenceRepository(),
                policy_provider=StubPolicyProvider(),
            )
        with self.assertRaisesRegex(TypeError, "filing evidence repository"):
            CorporateFilingsAnalysisRequestAssembler(
                filing_repository=StubFilingRepository(package()),
                evidence_repository=object(),
                policy_provider=StubPolicyProvider(),
            )

        with self.assertRaisesRegex(TypeError, "verified policy provider"):
            CorporateFilingsAnalysisRequestAssembler(
                filing_repository=StubFilingRepository(package()),
                evidence_repository=StubEvidenceRepository(),
                policy_provider=object(),
            )

    def test_caller_cannot_supply_or_override_analysis_policy(self):
        payload = assembly_input().model_dump(exclude_computed_fields=True)
        with self.assertRaises(ValidationError):
            CorporateFilingsAnalysisAssemblyInput.model_validate(
                {**payload, "policy": analysis_policy()}
            )

        provider = StubPolicyProvider(policy=object())
        service, _, _, _ = assembler(policy_provider=provider)
        with self.assertRaisesRegex(
            CorporateFilingsAnalysisAssemblyError,
            "verified filing-analysis policy",
        ):
            service.assemble(assembly_input())

        self.assertIsInstance(provider, CorporateFilingsAnalysisPolicyProvider)


if __name__ == "__main__":
    unittest.main()
