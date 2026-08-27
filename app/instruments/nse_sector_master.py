import csv
import io
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import Any, NamedTuple

import requests
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.exceptions import (
    InstrumentMasterDataError,
    InstrumentMasterDownloadError,
)
from app.logging_config import get_logger


DEFAULT_NSE_SECTOR_MASTER_URL = (
    "https://nsearchives.nseindia.com/content/equity/EQUITY_L.csv"
)
DEFAULT_NSE_SECTOR_MASTER_CACHE_PATH = Path(
    "data/cache/nse_sector_master.json"
)

SectorDownloader = Callable[[str, float, int], bytes]
logger = get_logger(__name__)


class SectorIndustry(NamedTuple):
    sector: str
    industry: str


class NseSectorMasterConfig(BaseModel):
    """Validated download and cache controls for NSE's equity master list.

    NSE's own equity master keys sector/industry by the exact tradable
    symbol, so this data joins onto Angel's instrument identity directly
    -- no fuzzy matching is required here (contrast with amfi_market_cap.py,
    which is keyed by company name).
    """

    model_config = ConfigDict(frozen=True, strict=True)

    endpoint_url: str = Field(
        default=DEFAULT_NSE_SECTOR_MASTER_URL,
        min_length=1,
    )
    cache_path: Path = DEFAULT_NSE_SECTOR_MASTER_CACHE_PATH
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
            raise ValueError("NSE sector-master endpoint must use HTTPS")
        return normalized

    @field_validator("cache_path")
    @classmethod
    def require_file_cache_path(cls, value: Path) -> Path:
        if not str(value).strip() or value.name in {"", ".", ".."}:
            raise ValueError("NSE sector-master cache path must name a file")
        return value

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "NseSectorMasterConfig":
        source = environment if environment is not None else os.environ
        values: dict[str, Any] = {}
        mappings = {
            "NSE_SECTOR_MASTER_URL": "endpoint_url",
            "NSE_SECTOR_MASTER_CACHE_PATH": "cache_path",
            "NSE_SECTOR_MASTER_DOWNLOAD_TIMEOUT_SECONDS": (
                "download_timeout_seconds"
            ),
            "NSE_SECTOR_MASTER_MAX_PAYLOAD_BYTES": "max_payload_bytes",
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
                    "NSE sector-master max_payload_bytes must be an integer"
                ) from exc
        if "download_timeout_seconds" in values:
            try:
                values["download_timeout_seconds"] = float(
                    values["download_timeout_seconds"]
                )
            except ValueError as exc:
                raise ValueError(
                    "NSE sector-master download timeout must be numeric"
                ) from exc
        return cls.model_validate(values)


class NseSectorMasterCatalog:
    """Downloads/caches NSE's official equity-master sector/industry list.

    Payload format: UTF-8 CSV with a header row containing at least
    "SYMBOL", "SECTOR", and "INDUSTRY" columns (column names are matched
    case-insensitively so a future header casing change doesn't break
    parsing).
    """

    def __init__(
        self,
        config: NseSectorMasterConfig | None = None,
        *,
        downloader: SectorDownloader | None = None,
    ) -> None:
        resolved_config = config or NseSectorMasterConfig()
        if not isinstance(resolved_config, NseSectorMasterConfig):
            raise ValueError(
                "NSE sector-master catalog requires validated configuration"
            )
        if downloader is not None and not callable(downloader):
            raise ValueError("NSE sector-master downloader must be callable")
        self.config = resolved_config
        self._downloader = downloader or _download_sector_master
        self._mapping: dict[str, SectorIndustry] | None = None
        self._lock = RLock()

    def sector_industry_by_symbol(self) -> Mapping[str, SectorIndustry]:
        with self._lock:
            if self._mapping is not None:
                return self._mapping

            cached_payload = _read_cache(
                self.config.cache_path,
                self.config.max_payload_bytes,
            )
            if cached_payload is not None:
                try:
                    mapping = _parse_sector_master_payload(cached_payload)
                except InstrumentMasterDataError:
                    logger.warning(
                        "Cached NSE sector-master payload was invalid",
                        extra={
                            "event": "nse.sector_master.cache_invalid",
                            "cache_path": str(self.config.cache_path),
                        },
                    )
                else:
                    self._mapping = dict(mapping)
                    return self._mapping

            try:
                payload = self._download()
            except InstrumentMasterDownloadError:
                stale_payload = _read_cache(
                    self.config.cache_path,
                    self.config.max_payload_bytes,
                )
                if stale_payload is None:
                    raise
                mapping = _parse_sector_master_payload(stale_payload)
                logger.warning(
                    "Using stale NSE sector-master cache after download "
                    "failure",
                    extra={
                        "event": "nse.sector_master.stale_cache_used",
                        "symbol_count": len(mapping),
                        "cache_path": str(self.config.cache_path),
                    },
                )
            else:
                mapping = _parse_sector_master_payload(payload)
                _write_cache_atomically(self.config.cache_path, payload)

            self._mapping = dict(mapping)
            return self._mapping

    def refresh(self) -> int:
        """Force a validated download and atomically replace the cache.

        The cache is otherwise sticky (no automatic expiry) -- this is
        the only way to pick up an updated NSE equity master.
        """
        with self._lock:
            payload = self._download()
            mapping = _parse_sector_master_payload(payload)
            _write_cache_atomically(self.config.cache_path, payload)
            self._mapping = dict(mapping)
            logger.info(
                "NSE sector master refreshed",
                extra={
                    "event": "nse.sector_master.refreshed",
                    "symbol_count": len(mapping),
                    "cache_path": str(self.config.cache_path),
                },
            )
            return len(mapping)

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
                "Unable to download the NSE sector master"
            ) from exc
        if not isinstance(payload, bytes):
            raise InstrumentMasterDataError(
                "NSE sector-master downloader returned an invalid payload"
            )
        if not payload or len(payload) > self.config.max_payload_bytes:
            raise InstrumentMasterDataError(
                "NSE sector-master payload size is invalid"
            )
        return payload


_BROWSER_LIKE_HEADERS = {
    # NSE's archive host hangs until the client read-timeout on requests
    # that don't look like a browser -- it never returns a 4xx/5xx to
    # reject them, it just never responds. A realistic User-Agent avoids
    # that hang; this is not a spoofing concern since these are the same
    # public archive files a browser would download.
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
}


def _download_sector_master(
    url: str,
    timeout_seconds: float,
    max_payload_bytes: int,
) -> bytes:
    try:
        with requests.get(
            url,
            timeout=timeout_seconds,
            stream=True,
            headers=_BROWSER_LIKE_HEADERS,
        ) as response:
            response.raise_for_status()
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    declared_size = int(content_length)
                except ValueError as exc:
                    raise InstrumentMasterDataError(
                        "NSE sector-master response has an invalid size"
                    ) from exc
                if declared_size > max_payload_bytes:
                    raise InstrumentMasterDataError(
                        "NSE sector-master response exceeds the size limit"
                    )
            chunks: list[bytes] = []
            received = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                received += len(chunk)
                if received > max_payload_bytes:
                    raise InstrumentMasterDataError(
                        "NSE sector-master response exceeds the size limit"
                    )
                chunks.append(chunk)
    except requests.RequestException as exc:
        raise InstrumentMasterDownloadError(
            "Unable to download the NSE sector master"
        ) from exc
    return b"".join(chunks)


def _parse_sector_master_payload(payload: bytes) -> dict[str, SectorIndustry]:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InstrumentMasterDataError(
            "NSE sector-master payload is not valid UTF-8 CSV"
        ) from exc

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise InstrumentMasterDataError(
            "NSE sector-master payload has no header row"
        )
    normalized_fields = {
        (name or "").strip().lower(): name for name in reader.fieldnames
    }
    symbol_column = normalized_fields.get("symbol")
    sector_column = normalized_fields.get("sector")
    industry_column = normalized_fields.get("industry")
    if symbol_column is None or sector_column is None or industry_column is None:
        raise InstrumentMasterDataError(
            "NSE sector-master payload is missing required columns"
        )

    mapping: dict[str, SectorIndustry] = {}
    for row in reader:
        symbol = (row.get(symbol_column) or "").strip().upper()
        sector = (row.get(sector_column) or "").strip()
        industry = (row.get(industry_column) or "").strip()
        if not symbol or not sector or not industry:
            continue
        if symbol in mapping:
            raise InstrumentMasterDataError(
                "NSE sector-master payload contains a duplicate symbol"
            )
        mapping[symbol] = SectorIndustry(sector=sector, industry=industry)

    if not mapping:
        raise InstrumentMasterDataError(
            "NSE sector-master payload contains no classified symbols"
        )
    return mapping


def _read_cache(path: Path, max_payload_bytes: int) -> bytes | None:
    try:
        if path.stat().st_size > max_payload_bytes:
            raise InstrumentMasterDataError(
                "NSE sector-master cache exceeds the configured size limit"
            )
        payload = path.read_bytes()
    except FileNotFoundError:
        return None
    except InstrumentMasterDataError:
        raise
    except OSError as exc:
        raise InstrumentMasterDataError(
            "Unable to read the NSE sector-master cache"
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
            "Unable to update the NSE sector-master cache"
        ) from exc
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink(missing_ok=True)
