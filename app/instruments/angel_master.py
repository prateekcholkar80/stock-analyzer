import json
import os
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import Any

import requests
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from app.exceptions import (
    InstrumentMasterDataError,
    InstrumentMasterDownloadError,
)
from app.instruments.in_memory import InMemoryInstrumentResolver
from app.logging_config import get_logger
from app.models.instruments import ResolvedInstrument


DEFAULT_ANGEL_INSTRUMENT_MASTER_URL = (
    "https://margincalculator.angelone.in/"
    "OpenAPI_File/files/OpenAPIScripMaster.json"
)
DEFAULT_ANGEL_INSTRUMENT_CACHE_PATH = Path(
    "data/cache/angel_instrument_master.json"
)

InstrumentMasterDownloader = Callable[[str, float, int], bytes]
Clock = Callable[[], float]
logger = get_logger(__name__)


class AngelInstrumentMasterConfig(BaseModel):
    """Validated download, cache, and cash-market filtering controls."""

    model_config = ConfigDict(frozen=True, strict=True)

    endpoint_url: str = Field(
        default=DEFAULT_ANGEL_INSTRUMENT_MASTER_URL,
        min_length=1,
    )
    cache_path: Path = DEFAULT_ANGEL_INSTRUMENT_CACHE_PATH
    cache_ttl_seconds: int = Field(default=86_400, ge=0)
    download_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    max_payload_bytes: int = Field(
        default=100 * 1024 * 1024,
        ge=1_024,
    )
    exchanges: tuple[str, ...] = ("NSE",)

    @field_validator("endpoint_url")
    @classmethod
    def require_https_endpoint(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.lower().startswith("https://"):
            raise ValueError("Angel instrument endpoint must use HTTPS")
        return normalized

    @field_validator("cache_path")
    @classmethod
    def require_file_cache_path(cls, value: Path) -> Path:
        if not str(value).strip() or value.name in {"", ".", ".."}:
            raise ValueError("Angel instrument cache path must name a file")
        return value

    @field_validator("exchanges")
    @classmethod
    def normalize_exchanges(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip().upper() for value in values)
        if not normalized or any(not value for value in normalized):
            raise ValueError("Angel instrument exchanges must not be empty")
        if len(normalized) != len(set(normalized)):
            raise ValueError("Angel instrument exchanges must be unique")
        return normalized

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "AngelInstrumentMasterConfig":
        source = environment if environment is not None else os.environ
        values: dict[str, Any] = {}
        mappings = {
            "ANGEL_INSTRUMENT_MASTER_URL": "endpoint_url",
            "ANGEL_INSTRUMENT_CACHE_PATH": "cache_path",
            "ANGEL_INSTRUMENT_CACHE_TTL_SECONDS": "cache_ttl_seconds",
            "ANGEL_INSTRUMENT_DOWNLOAD_TIMEOUT_SECONDS": (
                "download_timeout_seconds"
            ),
            "ANGEL_INSTRUMENT_MAX_PAYLOAD_BYTES": "max_payload_bytes",
        }
        for environment_name, field_name in mappings.items():
            value = source.get(environment_name)
            if value is not None and value.strip():
                values[field_name] = value
        if "cache_path" in values:
            values["cache_path"] = Path(values["cache_path"])
        for field_name in ("cache_ttl_seconds", "max_payload_bytes"):
            if field_name in values:
                try:
                    values[field_name] = int(values[field_name])
                except ValueError as exc:
                    raise ValueError(
                        f"Angel instrument {field_name} must be an integer"
                    ) from exc
        if "download_timeout_seconds" in values:
            try:
                values["download_timeout_seconds"] = float(
                    values["download_timeout_seconds"]
                )
            except ValueError as exc:
                raise ValueError(
                    "Angel instrument download timeout must be numeric"
                ) from exc
        exchanges = source.get("ANGEL_INSTRUMENT_EXCHANGES")
        if exchanges is not None and exchanges.strip():
            values["exchanges"] = tuple(exchanges.split(","))
        return cls.model_validate(values)


class AngelInstrumentMasterResolver:
    """Resolve cash instruments from Angel One's cached daily master."""

    def __init__(
        self,
        config: AngelInstrumentMasterConfig | None = None,
        *,
        downloader: InstrumentMasterDownloader | None = None,
        clock: Clock = time.time,
    ) -> None:
        resolved_config = config or AngelInstrumentMasterConfig()
        if not isinstance(resolved_config, AngelInstrumentMasterConfig):
            raise ValueError(
                "Angel instrument resolver requires validated configuration"
            )
        if downloader is not None and not callable(downloader):
            raise ValueError("Angel instrument downloader must be callable")
        if not callable(clock):
            raise ValueError("Angel instrument cache clock must be callable")
        self.config = resolved_config
        self._downloader = downloader or _download_instrument_master
        self._clock = clock
        self._resolver: InMemoryInstrumentResolver | None = None
        self._lock = RLock()

    def resolve(
        self,
        query: str,
        *,
        exchange: str | None = None,
    ) -> ResolvedInstrument:
        return self._catalog_resolver().resolve(query, exchange=exchange)

    def refresh(self) -> int:
        """Force a validated download and atomically replace the cache."""
        with self._lock:
            payload = self._download()
            instruments = _parse_instruments(payload, self.config)
            _write_cache_atomically(self.config.cache_path, payload)
            self._resolver = InMemoryInstrumentResolver(instruments)
            logger.info(
                "Angel instrument master refreshed",
                extra={
                    "event": "angel.instrument_master.refreshed",
                    "instrument_count": len(instruments),
                    "cache_path": str(self.config.cache_path),
                },
            )
            return len(instruments)

    def _catalog_resolver(self) -> InMemoryInstrumentResolver:
        with self._lock:
            if self._resolver is not None:
                return self._resolver

            cached_payload = _read_cache_if_fresh(
                self.config,
                now=self._clock(),
            )
            if cached_payload is not None:
                try:
                    instruments = _parse_instruments(
                        cached_payload,
                        self.config,
                    )
                except InstrumentMasterDataError:
                    logger.warning(
                        "Fresh Angel instrument cache was invalid",
                        extra={
                            "event": "angel.instrument_master.cache_invalid",
                            "cache_path": str(self.config.cache_path),
                        },
                    )
                else:
                    self._resolver = InMemoryInstrumentResolver(instruments)
                    return self._resolver

            try:
                payload = self._download()
            except InstrumentMasterDownloadError:
                stale_payload = _read_cache(
                    self.config.cache_path,
                    self.config.max_payload_bytes,
                )
                if stale_payload is None:
                    raise
                instruments = _parse_instruments(
                    stale_payload,
                    self.config,
                )
                logger.warning(
                    "Using stale Angel instrument cache after download failure",
                    extra={
                        "event": "angel.instrument_master.stale_cache_used",
                        "instrument_count": len(instruments),
                        "cache_path": str(self.config.cache_path),
                    },
                )
            else:
                instruments = _parse_instruments(payload, self.config)
                _write_cache_atomically(self.config.cache_path, payload)

            self._resolver = InMemoryInstrumentResolver(instruments)
            return self._resolver

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
                "Unable to download the Angel instrument master"
            ) from exc
        if not isinstance(payload, bytes):
            raise InstrumentMasterDataError(
                "Angel instrument downloader returned an invalid payload"
            )
        if not payload or len(payload) > self.config.max_payload_bytes:
            raise InstrumentMasterDataError(
                "Angel instrument payload size is invalid"
            )
        return payload


def _download_instrument_master(
    url: str,
    timeout_seconds: float,
    max_payload_bytes: int,
) -> bytes:
    try:
        with requests.get(
            url,
            timeout=timeout_seconds,
            stream=True,
        ) as response:
            response.raise_for_status()
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    declared_size = int(content_length)
                except ValueError as exc:
                    raise InstrumentMasterDataError(
                        "Angel instrument response has an invalid size"
                    ) from exc
                if declared_size > max_payload_bytes:
                    raise InstrumentMasterDataError(
                        "Angel instrument response exceeds the size limit"
                    )
            chunks: list[bytes] = []
            received = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                received += len(chunk)
                if received > max_payload_bytes:
                    raise InstrumentMasterDataError(
                        "Angel instrument response exceeds the size limit"
                    )
                chunks.append(chunk)
    except requests.RequestException as exc:
        raise InstrumentMasterDownloadError(
            "Unable to download the Angel instrument master"
        ) from exc
    return b"".join(chunks)


def _parse_instruments(
    payload: bytes,
    config: AngelInstrumentMasterConfig,
) -> tuple[ResolvedInstrument, ...]:
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstrumentMasterDataError(
            "Angel instrument master is not valid JSON"
        ) from exc
    if not isinstance(decoded, list):
        raise InstrumentMasterDataError(
            "Angel instrument master must contain a JSON list"
        )

    allowed_exchanges = set(config.exchanges)
    instruments: list[ResolvedInstrument] = []
    identities: set[tuple[str, str]] = set()
    for row in decoded:
        if not isinstance(row, dict):
            continue
        exchange = _row_text(row, "exch_seg", required=False).upper()
        if exchange not in allowed_exchanges:
            continue
        instrument_type = _row_text(
            row,
            "instrumenttype",
            required=False,
        ).upper()
        symbol = _row_text(row, "symbol", required=True)
        if instrument_type or not _is_cash_equity(exchange, symbol):
            continue
        token = _row_text(row, "token", required=True)
        name = _row_text(row, "name", required=True)
        identity = (exchange.casefold(), token.casefold())
        if identity in identities:
            raise InstrumentMasterDataError(
                "Angel instrument master contains duplicate identities"
            )
        identities.add(identity)
        try:
            instruments.append(
                ResolvedInstrument(
                    exchange=exchange,
                    symbol_token=token,
                    symbol=symbol,
                    display_name=name,
                )
            )
        except ValidationError as exc:
            raise InstrumentMasterDataError(
                "Angel instrument master contains an invalid cash instrument"
            ) from exc
    if not instruments:
        raise InstrumentMasterDataError(
            "Angel instrument master contains no eligible cash instruments"
        )
    return tuple(instruments)


def _row_text(
    row: dict[str, Any],
    key: str,
    *,
    required: bool,
) -> str:
    value = row.get(key, "")
    if value is None:
        value = ""
    if not isinstance(value, (str, int)):
        if required:
            raise InstrumentMasterDataError(
                "Angel instrument master contains an invalid field"
            )
        return ""
    normalized = str(value).strip()
    if required and not normalized:
        raise InstrumentMasterDataError(
            "Angel instrument master contains a missing required field"
        )
    return normalized


def _is_cash_equity(exchange: str, symbol: str) -> bool:
    if exchange == "NSE":
        return symbol.upper().endswith("-EQ")
    return exchange == "BSE"


def _read_cache_if_fresh(
    config: AngelInstrumentMasterConfig,
    *,
    now: float,
) -> bytes | None:
    try:
        modified_at = config.cache_path.stat().st_mtime
    except OSError:
        return None
    age = now - modified_at
    if age < 0 or age > config.cache_ttl_seconds:
        return None
    return _read_cache(config.cache_path, config.max_payload_bytes)


def _read_cache(path: Path, max_payload_bytes: int) -> bytes | None:
    try:
        if path.stat().st_size > max_payload_bytes:
            raise InstrumentMasterDataError(
                "Angel instrument cache exceeds the configured size limit"
            )
        payload = path.read_bytes()
    except FileNotFoundError:
        return None
    except InstrumentMasterDataError:
        raise
    except OSError as exc:
        raise InstrumentMasterDataError(
            "Unable to read the Angel instrument cache"
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
            "Unable to update the Angel instrument cache"
        ) from exc
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink(missing_ok=True)
