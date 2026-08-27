import io
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import Any, NamedTuple

import pandas as pd
import requests
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.exceptions import (
    InstrumentMasterDataError,
    InstrumentMasterDownloadError,
)
from app.instruments.classification import MarketCapClass
from app.logging_config import get_logger


# AMFI publishes the SEBI-mandated large/mid/small-cap classification as a
# biannual Excel/PDF pair, listed under a per-year accordion at this page --
# there is no single stable direct-download file URL (the actual .xlsx link
# changes per half-year release, e.g. "Jan - June" / "July - Dec"). This
# constant is the direct .xlsx link for AMFI's current published release
# (2026 Jan-Jun, from portal.amfiindia.com -- a different subdomain than
# the www.amfiindia.com landing page that links to it); it is not
# resolved automatically by this module and must be updated (or
# overridden via AmfiMarketCapConfig.endpoint_url) whenever AMFI
# publishes their next biannual release, since it does not follow a
# predictable URL pattern release to release.
DEFAULT_AMFI_MARKET_CAP_URL = (
    "https://portal.amfiindia.com/spages/"
    "AverageMarketCapitalization30Jun2026.xlsx"
)
DEFAULT_AMFI_MARKET_CAP_CACHE_PATH = Path(
    "data/cache/amfi_market_cap.json"
)

_CATEGORY_TO_MARKET_CAP_CLASS = {
    "large cap": MarketCapClass.LARGE_CAP,
    "mid cap": MarketCapClass.MID_CAP,
    "small cap": MarketCapClass.SMALL_CAP,
}

_HEADER_SEARCH_ROW_LIMIT = 10

MarketCapDownloader = Callable[[str, float, int], bytes]
logger = get_logger(__name__)


class AmfiMarketCapEntry(NamedTuple):
    """One AMFI-classified company. nse_symbol is None when AMFI's own
    "NSE Symbol" column is blank/"-" for that company (e.g. a BSE-only
    listing) -- in that case joining onto an NSE instrument catalog must
    fall back to fuzzy company-name matching rather than a direct key.
    """

    company_name: str
    nse_symbol: str | None
    market_cap_class: MarketCapClass


class AmfiMarketCapConfig(BaseModel):
    """Validated download and cache controls for AMFI's cap-class list.

    The exact current AMFI file URL/format is published per SEBI-mandated
    biannual release and can change; endpoint_url is swappable so a
    release change does not require a code change.
    """

    model_config = ConfigDict(frozen=True, strict=True)

    endpoint_url: str = Field(
        default=DEFAULT_AMFI_MARKET_CAP_URL,
        min_length=1,
    )
    cache_path: Path = DEFAULT_AMFI_MARKET_CAP_CACHE_PATH
    download_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    max_payload_bytes: int = Field(
        default=20 * 1024 * 1024,
        ge=1_024,
    )

    @field_validator("endpoint_url")
    @classmethod
    def require_https_endpoint(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.lower().startswith("https://"):
            raise ValueError("AMFI market-cap endpoint must use HTTPS")
        return normalized

    @field_validator("cache_path")
    @classmethod
    def require_file_cache_path(cls, value: Path) -> Path:
        if not str(value).strip() or value.name in {"", ".", ".."}:
            raise ValueError("AMFI market-cap cache path must name a file")
        return value

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "AmfiMarketCapConfig":
        source = environment if environment is not None else os.environ
        values: dict[str, Any] = {}
        mappings = {
            "AMFI_MARKET_CAP_URL": "endpoint_url",
            "AMFI_MARKET_CAP_CACHE_PATH": "cache_path",
            "AMFI_MARKET_CAP_DOWNLOAD_TIMEOUT_SECONDS": (
                "download_timeout_seconds"
            ),
            "AMFI_MARKET_CAP_MAX_PAYLOAD_BYTES": "max_payload_bytes",
        }
        for environment_name, field_name in mappings.items():
            value = source.get(environment_name)
            if value is not None and value.strip():
                values[field_name] = value
        if "cache_path" in values:
            values["cache_path"] = Path(values["cache_path"])
        if "max_payload_bytes" in values:
            try:
                values["max_payload_bytes"] = int(values["max_payload_bytes"])
            except ValueError as exc:
                raise ValueError(
                    "AMFI market-cap max_payload_bytes must be an integer"
                ) from exc
        if "download_timeout_seconds" in values:
            try:
                values["download_timeout_seconds"] = float(
                    values["download_timeout_seconds"]
                )
            except ValueError as exc:
                raise ValueError(
                    "AMFI market-cap download timeout must be numeric"
                ) from exc
        return cls.model_validate(values)


class AmfiMarketCapCatalog:
    """Downloads/caches AMFI's official large/mid/small-cap classification.

    Payload format: an .xlsx workbook (as published by AMFI). AMFI's
    published workbook has a title row above the real header (e.g.
    "Average Market Capitalization of listed companies during the six
    months ended 30 June 2026"), so the header row is located by scanning
    the first few rows for one containing both a company-name-like and a
    category-like column, rather than assuming row 0 is the header.
    Required columns (matched by substring, case-insensitive): a
    "company"-containing column and a "categ"-containing column (AMFI's
    exact wording, e.g. "Categorization as per SEBI Circular...", varies
    by release). An "NSE Symbol" column is captured when present -- most
    small-cap rows are BSE-only listings with no NSE symbol ("-"), so this
    is optional per row, not a required column.
    """

    def __init__(
        self,
        config: AmfiMarketCapConfig | None = None,
        *,
        downloader: MarketCapDownloader | None = None,
    ) -> None:
        resolved_config = config or AmfiMarketCapConfig()
        if not isinstance(resolved_config, AmfiMarketCapConfig):
            raise ValueError(
                "AMFI market-cap catalog requires validated configuration"
            )
        if downloader is not None and not callable(downloader):
            raise ValueError("AMFI market-cap downloader must be callable")
        self.config = resolved_config
        self._downloader = downloader or _download_market_cap_list
        self._entries: tuple[AmfiMarketCapEntry, ...] | None = None
        self._lock = RLock()

    def entries(self) -> tuple[AmfiMarketCapEntry, ...]:
        with self._lock:
            if self._entries is not None:
                return self._entries

            cached_payload = _read_cache(
                self.config.cache_path,
                self.config.max_payload_bytes,
            )
            if cached_payload is not None:
                try:
                    entries = _parse_market_cap_payload(cached_payload)
                except InstrumentMasterDataError:
                    logger.warning(
                        "Cached AMFI market-cap payload was invalid",
                        extra={
                            "event": "amfi.market_cap.cache_invalid",
                            "cache_path": str(self.config.cache_path),
                        },
                    )
                else:
                    self._entries = entries
                    return self._entries

            try:
                payload = self._download()
            except InstrumentMasterDownloadError:
                stale_payload = _read_cache(
                    self.config.cache_path,
                    self.config.max_payload_bytes,
                )
                if stale_payload is None:
                    raise
                entries = _parse_market_cap_payload(stale_payload)
                logger.warning(
                    "Using stale AMFI market-cap cache after download failure",
                    extra={
                        "event": "amfi.market_cap.stale_cache_used",
                        "company_count": len(entries),
                        "cache_path": str(self.config.cache_path),
                    },
                )
            else:
                entries = _parse_market_cap_payload(payload)
                _write_cache_atomically(self.config.cache_path, payload)

            self._entries = entries
            return self._entries

    def refresh(self) -> int:
        """Force a validated download and atomically replace the cache.

        The cache is otherwise sticky (no automatic expiry) -- this is
        the only way to pick up a new AMFI release.
        """
        with self._lock:
            payload = self._download()
            entries = _parse_market_cap_payload(payload)
            _write_cache_atomically(self.config.cache_path, payload)
            self._entries = entries
            logger.info(
                "AMFI market-cap classification refreshed",
                extra={
                    "event": "amfi.market_cap.refreshed",
                    "company_count": len(entries),
                    "cache_path": str(self.config.cache_path),
                },
            )
            return len(entries)

    def _download(self) -> bytes:
        try:
            payload = self._downloader(
                self.config.endpoint_url,
                self.config.download_timeout_seconds,
                self.config.max_payload_bytes,
            )
        except InstrumentMasterDownloadError:
            raise
        except Exception as exc:
            raise InstrumentMasterDownloadError(
                "Unable to download the AMFI market-cap classification"
            ) from exc
        if not isinstance(payload, bytes):
            raise InstrumentMasterDataError(
                "AMFI market-cap downloader returned an invalid payload"
            )
        if not payload or len(payload) > self.config.max_payload_bytes:
            raise InstrumentMasterDataError(
                "AMFI market-cap payload size is invalid"
            )
        return payload


def _download_market_cap_list(
    url: str,
    timeout_seconds: float,
    max_payload_bytes: int,
) -> bytes:
    try:
        with requests.get(url, timeout=timeout_seconds, stream=True) as response:
            response.raise_for_status()
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    declared_size = int(content_length)
                except ValueError as exc:
                    raise InstrumentMasterDataError(
                        "AMFI market-cap response has an invalid size"
                    ) from exc
                if declared_size > max_payload_bytes:
                    raise InstrumentMasterDataError(
                        "AMFI market-cap response exceeds the size limit"
                    )
            chunks: list[bytes] = []
            received = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                received += len(chunk)
                if received > max_payload_bytes:
                    raise InstrumentMasterDataError(
                        "AMFI market-cap response exceeds the size limit"
                    )
                chunks.append(chunk)
    except requests.RequestException as exc:
        raise InstrumentMasterDownloadError(
            "Unable to download the AMFI market-cap classification"
        ) from exc
    return b"".join(chunks)


def _find_header_row(raw: pd.DataFrame) -> tuple[int, dict[str, int]]:
    """Locate the real header row and required column positions.

    AMFI's workbook has a title row above the header, so row 0 cannot be
    assumed to be the header. Scans the first _HEADER_SEARCH_ROW_LIMIT
    rows for one whose cells include both a "company"-like and a
    "categ"-like value.
    """
    search_limit = min(_HEADER_SEARCH_ROW_LIMIT, len(raw))
    for row_index in range(search_limit):
        cells = {
            str(value).strip().lower(): position
            for position, value in enumerate(raw.iloc[row_index])
            if isinstance(value, str)
        }
        company_position = next(
            (
                position
                for text, position in cells.items()
                if "company" in text
            ),
            None,
        )
        category_position = next(
            (position for text, position in cells.items() if "categ" in text),
            None,
        )
        if company_position is not None and category_position is not None:
            nse_symbol_position = next(
                (
                    position
                    for text, position in cells.items()
                    if text == "nse symbol"
                ),
                None,
            )
            positions = {"company": company_position, "category": category_position}
            if nse_symbol_position is not None:
                positions["nse_symbol"] = nse_symbol_position
            return row_index, positions

    raise InstrumentMasterDataError(
        "AMFI market-cap payload is missing required columns"
    )


def _parse_market_cap_payload(
    payload: bytes,
) -> tuple[AmfiMarketCapEntry, ...]:
    try:
        raw = pd.read_excel(
            io.BytesIO(payload),
            sheet_name=0,
            header=None,
            dtype=str,
        )
    except Exception as exc:
        raise InstrumentMasterDataError(
            "AMFI market-cap payload is not a valid .xlsx workbook"
        ) from exc

    header_row, positions = _find_header_row(raw)
    company_position = positions["company"]
    category_position = positions["category"]
    nse_symbol_position = positions.get("nse_symbol")

    entries: list[AmfiMarketCapEntry] = []
    seen_company_names: set[str] = set()
    for _, row in raw.iloc[header_row + 1 :].iterrows():
        company_name = str(row.iloc[company_position] or "").strip()
        category_text = str(row.iloc[category_position] or "").strip().lower()
        if not company_name or not category_text or company_name == "nan":
            continue
        market_cap_class = _CATEGORY_TO_MARKET_CAP_CLASS.get(category_text)
        if market_cap_class is None:
            raise InstrumentMasterDataError(
                "AMFI market-cap payload contains an unrecognized category"
            )
        if company_name in seen_company_names:
            raise InstrumentMasterDataError(
                "AMFI market-cap payload contains a duplicate company name"
            )
        seen_company_names.add(company_name)

        nse_symbol: str | None = None
        if nse_symbol_position is not None:
            raw_symbol = str(row.iloc[nse_symbol_position] or "").strip()
            if raw_symbol and raw_symbol != "-" and raw_symbol.lower() != "nan":
                nse_symbol = raw_symbol.upper()

        entries.append(
            AmfiMarketCapEntry(
                company_name=company_name,
                nse_symbol=nse_symbol,
                market_cap_class=market_cap_class,
            )
        )

    if not entries:
        raise InstrumentMasterDataError(
            "AMFI market-cap payload contains no classified companies"
        )
    return tuple(entries)


def _read_cache(path: Path, max_payload_bytes: int) -> bytes | None:
    try:
        if path.stat().st_size > max_payload_bytes:
            raise InstrumentMasterDataError(
                "AMFI market-cap cache exceeds the configured size limit"
            )
        payload = path.read_bytes()
    except FileNotFoundError:
        return None
    except InstrumentMasterDataError:
        raise
    except OSError as exc:
        raise InstrumentMasterDataError(
            "Unable to read the AMFI market-cap cache"
        ) from exc
    return payload or None


def _write_cache_atomically(path: Path, payload: bytes) -> None:
    temporary_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
    except OSError as exc:
        raise InstrumentMasterDataError(
            "Unable to update the AMFI market-cap cache"
        ) from exc
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink(missing_ok=True)
