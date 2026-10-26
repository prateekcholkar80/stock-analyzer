"""Immutable completeness contract for one company-wide fundamental refresh."""

from datetime import datetime
from enum import StrEnum
from hashlib import sha256
import json
from typing import Literal, Self

from pydantic import (
    Field,
    InstanceOf,
    computed_field,
    field_validator,
    model_validator,
)

from app.gateways.fundamentals import FundamentalCapability
from app.models.benchmarking_financials_storage import (
    StoredBenchmarkingFinancialsDocument,
)
from app.models.financial_document_storage import (
    StoredStructuredFinancialDocument,
)
from app.models.financial_documents import FinancialReportingBasis
from app.models.fundamental_storage import StoredFundamentalSnapshot
from app.models.fundamentals import (
    FundamentalIssuerIdentity,
    FundamentalModel,
    ProviderConnectionScope,
)
from app.models.peer_comparison_storage import StoredPeerComparisonDocument


_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
_FINGERPRINT_PATTERN = r"^[a-f0-9]{64}$"


class FundamentalPackageDataset(StrEnum):
    COMPANY_OVERVIEW = "company_overview"
    FINANCIAL_STATEMENTS = "financial_statements"
    SHAREHOLDING_HISTORY = "shareholding_history"
    GROWTH_TABLE = "growth_table"
    BALANCE_SHEET = "balance_sheet"
    PROFIT_AND_LOSS = "profit_and_loss"
    CASH_FLOW = "cash_flow"
    RATIOS = "ratios"
    QUARTERLY_RESULTS = "quarterly_results"
    PEER_COMPARISON = "peer_comparison"
    BENCHMARKING_FINANCIALS = "benchmarking_financials"


_DUAL_BASIS_DATASETS = {
    FundamentalPackageDataset.BALANCE_SHEET,
    FundamentalPackageDataset.PROFIT_AND_LOSS,
    FundamentalPackageDataset.CASH_FLOW,
    FundamentalPackageDataset.RATIOS,
    FundamentalPackageDataset.QUARTERLY_RESULTS,
}


class FundamentalPackageItemKey(FundamentalModel):
    """Provider-neutral identity for one required package slot."""

    dataset: FundamentalPackageDataset
    reporting_basis: FinancialReportingBasis

    @model_validator(mode="after")
    def validate_reporting_basis(self) -> Self:
        if self.dataset in _DUAL_BASIS_DATASETS:
            if self.reporting_basis not in {
                FinancialReportingBasis.CONSOLIDATED,
                FinancialReportingBasis.STANDALONE,
            }:
                raise ValueError(
                    "financial package dataset requires consolidated or "
                    "standalone reporting"
                )
        elif self.reporting_basis is not FinancialReportingBasis.NOT_APPLICABLE:
            raise ValueError(
                "non-financial package dataset requires not-applicable "
                "reporting"
            )
        return self

    @computed_field
    @property
    def canonical_key(self) -> str:
        return f"{self.dataset.value}:{self.reporting_basis.value}"


def _package_key(
    dataset: FundamentalPackageDataset,
    reporting_basis: FinancialReportingBasis = (
        FinancialReportingBasis.NOT_APPLICABLE
    ),
) -> FundamentalPackageItemKey:
    return FundamentalPackageItemKey(
        dataset=dataset,
        reporting_basis=reporting_basis,
    )


FUNDAMENTAL_PACKAGE_REQUIRED_ITEMS = (
    _package_key(FundamentalPackageDataset.COMPANY_OVERVIEW),
    _package_key(FundamentalPackageDataset.FINANCIAL_STATEMENTS),
    _package_key(FundamentalPackageDataset.SHAREHOLDING_HISTORY),
    _package_key(FundamentalPackageDataset.GROWTH_TABLE),
    *(
        _package_key(dataset, reporting_basis)
        for dataset in (
            FundamentalPackageDataset.BALANCE_SHEET,
            FundamentalPackageDataset.PROFIT_AND_LOSS,
            FundamentalPackageDataset.CASH_FLOW,
            FundamentalPackageDataset.RATIOS,
            FundamentalPackageDataset.QUARTERLY_RESULTS,
        )
        for reporting_basis in (
            FinancialReportingBasis.CONSOLIDATED,
            FinancialReportingBasis.STANDALONE,
        )
    ),
    _package_key(FundamentalPackageDataset.PEER_COMPARISON),
    _package_key(FundamentalPackageDataset.BENCHMARKING_FINANCIALS),
)

_REQUIRED_CANONICAL_KEYS = frozenset(
    item.canonical_key for item in FUNDAMENTAL_PACKAGE_REQUIRED_ITEMS
)


class FundamentalPackageItem(FundamentalModel):
    """One validated cache artifact staged for package activation."""

    key: FundamentalPackageItemKey
    cache_entry_id: str = Field(
        min_length=1,
        max_length=180,
        pattern=_ID_PATTERN,
    )
    content_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    retrieved_at: datetime
    expires_at: datetime

    @field_validator("retrieved_at", "expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("fundamental package timestamps require timezone")
        return value

    @model_validator(mode="after")
    def validate_freshness(self) -> Self:
        if self.expires_at <= self.retrieved_at:
            raise ValueError("fundamental package item expiry must follow retrieval")
        return self


class FundamentalPackageManifest(FundamentalModel):
    """Commit record released only when every required artifact is present."""

    schema_version: Literal["jarvis.fundamental_package_manifest.v1"] = (
        "jarvis.fundamental_package_manifest.v1"
    )
    operation_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=_ID_PATTERN,
    )
    connection: ProviderConnectionScope
    issuer: FundamentalIssuerIdentity
    requested_at: datetime
    committed_at: datetime
    items: tuple[FundamentalPackageItem, ...] = Field(
        min_length=len(FUNDAMENTAL_PACKAGE_REQUIRED_ITEMS),
        max_length=len(FUNDAMENTAL_PACKAGE_REQUIRED_ITEMS),
    )

    @field_validator("requested_at", "committed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("fundamental package timestamps require timezone")
        return value

    @model_validator(mode="after")
    def validate_commit_barrier(self) -> Self:
        canonical_keys = tuple(item.key.canonical_key for item in self.items)
        if len(canonical_keys) != len(set(canonical_keys)):
            raise ValueError("fundamental package item keys must be unique")
        if set(canonical_keys) != _REQUIRED_CANONICAL_KEYS:
            raise ValueError(
                "fundamental package requires every configured dataset"
            )
        cache_entry_ids = tuple(item.cache_entry_id for item in self.items)
        if len(cache_entry_ids) != len(set(cache_entry_ids)):
            raise ValueError(
                "fundamental package cache references must be unique"
            )
        if self.committed_at < self.requested_at:
            raise ValueError("package commit cannot predate its request")
        if any(item.retrieved_at < self.requested_at for item in self.items):
            raise ValueError("package item cannot predate its refresh request")
        if any(item.retrieved_at > self.committed_at for item in self.items):
            raise ValueError("package commit cannot predate item retrieval")
        if any(item.expires_at <= self.committed_at for item in self.items):
            raise ValueError("package cannot activate an expired item")
        return self

    @computed_field
    @property
    def expires_at(self) -> datetime:
        return min(item.expires_at for item in self.items)

    @computed_field
    @property
    def manifest_fingerprint(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "operation_id": self.operation_id,
            "connection": self.connection.model_dump(mode="json"),
            "issuer": self.issuer.model_dump(mode="json"),
            "requested_at": self.requested_at.isoformat(),
            "committed_at": self.committed_at.isoformat(),
            "items": sorted(
                (
                    {
                        "key": item.key.canonical_key,
                        "cache_entry_id": item.cache_entry_id,
                        "content_fingerprint": item.content_fingerprint,
                        "retrieved_at": item.retrieved_at.isoformat(),
                        "expires_at": item.expires_at.isoformat(),
                    }
                    for item in self.items
                ),
                key=lambda item: item["key"],
            ),
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(encoded).hexdigest()

    @computed_field
    @property
    def package_id(self) -> str:
        return f"fundamental_package:{self.manifest_fingerprint}"

    def is_expired(self, *, as_of: datetime) -> bool:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("fundamental package read time requires timezone")
        return as_of >= self.expires_at


_REQUIRED_SNAPSHOT_CAPABILITIES = frozenset(
    {
        FundamentalCapability.COMPANY_OVERVIEW,
        FundamentalCapability.FINANCIAL_STATEMENTS,
        FundamentalCapability.SHAREHOLDING_HISTORY,
    }
)


class FundamentalEvidencePackage(FundamentalModel):
    """One immutable, complete company evidence package ready for activation."""

    schema_version: Literal["jarvis.fundamental_evidence_package.v1"] = (
        "jarvis.fundamental_evidence_package.v1"
    )
    manifest: FundamentalPackageManifest
    snapshots: tuple[InstanceOf[StoredFundamentalSnapshot], ...] = Field(
        min_length=3,
        max_length=3,
    )
    structured_documents: tuple[
        InstanceOf[StoredStructuredFinancialDocument], ...
    ] = Field(min_length=11, max_length=11)
    peer_comparison: InstanceOf[StoredPeerComparisonDocument]
    benchmarking_financials: InstanceOf[
        StoredBenchmarkingFinancialsDocument
    ]

    @model_validator(mode="after")
    def validate_complete_package(self) -> Self:
        snapshot_capabilities = tuple(
            snapshot.request.capability for snapshot in self.snapshots
        )
        if (
            len(snapshot_capabilities) != len(set(snapshot_capabilities))
            or set(snapshot_capabilities) != _REQUIRED_SNAPSHOT_CAPABILITIES
        ):
            raise ValueError(
                "fundamental package requires one snapshot per required capability"
            )

        actual_items = tuple(self._artifact_items())
        actual_by_key = {item.key.canonical_key: item for item in actual_items}
        if len(actual_by_key) != len(actual_items):
            raise ValueError("fundamental package artifact scenarios must be unique")
        manifest_by_key = {
            item.key.canonical_key: item for item in self.manifest.items
        }
        if actual_by_key != manifest_by_key:
            raise ValueError(
                "fundamental package artifacts must exactly match the manifest"
            )

        for artifact in self._artifacts():
            request = artifact.request
            if request.connection != self.manifest.connection:
                raise ValueError(
                    "fundamental package artifacts must share the manifest connection"
                )
            if request.issuer != self.manifest.issuer:
                raise ValueError(
                    "fundamental package artifacts must share the manifest issuer"
                )
            if request.operation_id != self.manifest.operation_id:
                raise ValueError(
                    "fundamental package artifacts must share the manifest operation"
                )
        return self

    def _artifacts(self) -> tuple[
        StoredFundamentalSnapshot
        | StoredStructuredFinancialDocument
        | StoredPeerComparisonDocument
        | StoredBenchmarkingFinancialsDocument,
        ...,
    ]:
        return (
            *self.snapshots,
            *self.structured_documents,
            self.peer_comparison,
            self.benchmarking_financials,
        )

    def _artifact_items(self):
        for snapshot in self.snapshots:
            yield FundamentalPackageItem(
                key=FundamentalPackageItemKey(
                    dataset=FundamentalPackageDataset(
                        snapshot.request.capability.value
                    ),
                    reporting_basis=FinancialReportingBasis.NOT_APPLICABLE,
                ),
                cache_entry_id=snapshot.cache_key.cache_entry_id,
                content_fingerprint=snapshot.snapshot_fingerprint,
                retrieved_at=snapshot.retrieved_at,
                expires_at=snapshot.expires_at,
            )
        for document in self.structured_documents:
            yield FundamentalPackageItem(
                key=FundamentalPackageItemKey(
                    dataset=FundamentalPackageDataset(
                        document.request.document_type.value
                    ),
                    reporting_basis=document.request.reporting_basis,
                ),
                cache_entry_id=document.cache_key.cache_entry_id,
                content_fingerprint=document.document_fingerprint,
                retrieved_at=document.retrieved_at,
                expires_at=document.expires_at,
            )
        for dataset, document in (
            (FundamentalPackageDataset.PEER_COMPARISON, self.peer_comparison),
            (
                FundamentalPackageDataset.BENCHMARKING_FINANCIALS,
                self.benchmarking_financials,
            ),
        ):
            yield FundamentalPackageItem(
                key=FundamentalPackageItemKey(
                    dataset=dataset,
                    reporting_basis=FinancialReportingBasis.NOT_APPLICABLE,
                ),
                cache_entry_id=document.cache_key.cache_entry_id,
                content_fingerprint=document.document_fingerprint,
                retrieved_at=document.retrieved_at,
                expires_at=document.expires_at,
            )

    @computed_field
    @property
    def aggregate_fingerprint(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "manifest_fingerprint": self.manifest.manifest_fingerprint,
            "artifacts": sorted(
                (
                    {
                        "key": item.key.canonical_key,
                        "cache_entry_id": item.cache_entry_id,
                        "content_fingerprint": item.content_fingerprint,
                    }
                    for item in self._artifact_items()
                ),
                key=lambda item: item["key"],
            ),
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(encoded).hexdigest()

    @property
    def package_id(self) -> str:
        return self.manifest.package_id

    def is_expired(self, *, as_of: datetime) -> bool:
        return self.manifest.is_expired(as_of=as_of)
