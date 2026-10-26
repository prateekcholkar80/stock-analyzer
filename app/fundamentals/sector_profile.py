"""Adapters that supply cited sector evidence to fundamental analytics."""

from datetime import datetime, timedelta

from app.analytics.fundamental_sector_profile import (
    FundamentalSectorEvidence,
    FundamentalSectorSourceAuthority,
    FundamentalSectorTaxonomy,
)
from app.exceptions import ApplicationError
from app.instruments.nse_sector_master import NseSectorMasterCatalog
from app.models.fundamentals import FundamentalIssuerIdentity


DEFAULT_NSE_SECTOR_EVIDENCE_MAX_AGE = timedelta(days=45)


def _base_nse_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    for suffix in ("-EQ", "-BE", "-BZ"):
        if normalized.endswith(suffix):
            return normalized[: -len(suffix)]
    return normalized


class NseFundamentalSectorEvidenceResolver:
    """Translate an exact NSE master row into provider-neutral evidence."""

    def __init__(
        self,
        catalog: NseSectorMasterCatalog,
        *,
        max_age: timedelta = DEFAULT_NSE_SECTOR_EVIDENCE_MAX_AGE,
    ) -> None:
        if not isinstance(catalog, NseSectorMasterCatalog):
            raise ValueError("sector evidence resolver requires an NSE catalog")
        if max_age <= timedelta(0):
            raise ValueError("sector evidence max age must be positive")
        self._catalog = catalog
        self._max_age = max_age

    def resolve(
        self,
        *,
        issuer: FundamentalIssuerIdentity,
        symbol: str,
        as_of: datetime,
    ) -> tuple[FundamentalSectorEvidence, ...]:
        if not isinstance(issuer, FundamentalIssuerIdentity):
            raise ValueError("sector evidence requires a validated issuer")
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("sector evidence time requires timezone")
        base_symbol = _base_nse_symbol(symbol)
        if (
            issuer.exchange != "NSE"
            or _base_nse_symbol(issuer.symbol) != base_symbol
        ):
            raise ValueError(
                "sector evidence symbol must exactly match the NSE issuer"
            )
        try:
            observation = self._catalog.observation_for_symbol(base_symbol)
        except ApplicationError:
            return ()
        if observation is None:
            return ()
        return (
            FundamentalSectorEvidence(
                issuer=issuer,
                taxonomy=FundamentalSectorTaxonomy.NSE_SECTOR_INDUSTRY,
                source_authority=(
                    FundamentalSectorSourceAuthority.EXCHANGE_OFFICIAL
                ),
                source_reference_id=(
                    f"nse-sector-master:{observation.symbol}"
                ),
                source_name="NSE equity sector master",
                source_location=observation.source_location,
                source_record_fingerprint=(
                    observation.source_record_fingerprint
                ),
                sector=observation.sector,
                industry=observation.industry,
                retrieved_at=observation.retrieved_at,
                expires_at=observation.retrieved_at + self._max_age,
            ),
        )
