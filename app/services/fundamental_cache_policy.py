"""Deterministic cache-versus-provider policy for fundamental evidence."""

from collections.abc import Callable
from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal, Self

from pydantic import ConfigDict, model_validator

from app.models.fundamental_storage import (
    FundamentalSnapshotCacheKey,
    FundamentalSnapshotRequest,
    StoredFundamentalSnapshot,
)
from app.models.technical import TechnicalModel
from app.storage.fundamental_repositories import (
    FundamentalSnapshotRepository,
)


class FundamentalLoadAction(StrEnum):
    """The only permitted next actions for one evidence request."""

    USE_CACHE = "USE_CACHE"
    RETRIEVE_PROVIDER = "RETRIEVE_PROVIDER"


class FundamentalCacheDecision(TechnicalModel):
    """Explainable result of evaluating one request against its cache."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: Literal["jarvis.fundamental_cache_decision.v1"] = (
        "jarvis.fundamental_cache_decision.v1"
    )
    action: FundamentalLoadAction
    reason: Literal["cache_hit", "cache_miss", "explicit_refresh"]
    cache_key: FundamentalSnapshotCacheKey
    cached_snapshot: StoredFundamentalSnapshot | None = None

    @model_validator(mode="after")
    def validate_action_payload(self) -> Self:
        if self.action is FundamentalLoadAction.USE_CACHE:
            if self.reason != "cache_hit" or self.cached_snapshot is None:
                raise ValueError("cache reuse requires a cached snapshot")
        elif self.cached_snapshot is not None or self.reason == "cache_hit":
            raise ValueError("provider retrieval cannot release cached evidence")
        return self


class FundamentalCachePolicy:
    """Choose cache reuse or provider retrieval without invoking a provider.

    The repository remains responsible for expiry and tenant isolation. An
    explicit refresh deliberately bypasses the cache lookup so the caller must
    retrieve fresh provider evidence. Technical-only routing must avoid this
    policy entirely; that boundary belongs to the workflow coordinator.
    """

    def __init__(
        self,
        repository: FundamentalSnapshotRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(repository, FundamentalSnapshotRepository):
            raise TypeError("fundamental cache policy requires a repository")
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def decide(
        self,
        request: FundamentalSnapshotRequest,
        *,
        refresh_requested: bool = False,
    ) -> FundamentalCacheDecision:
        if not isinstance(refresh_requested, bool):
            raise TypeError("fundamental refresh flag must be boolean")
        key = FundamentalSnapshotCacheKey.from_request(request)

        if refresh_requested:
            return FundamentalCacheDecision(
                action=FundamentalLoadAction.RETRIEVE_PROVIDER,
                reason="explicit_refresh",
                cache_key=key,
            )

        cached = self._repository.get_fundamental_snapshot(
            key,
            scope=key.repository_scope,
            as_of=self._clock(),
        )
        if cached is None:
            return FundamentalCacheDecision(
                action=FundamentalLoadAction.RETRIEVE_PROVIDER,
                reason="cache_miss",
                cache_key=key,
            )
        return FundamentalCacheDecision(
            action=FundamentalLoadAction.USE_CACHE,
            reason="cache_hit",
            cache_key=key,
            cached_snapshot=cached,
        )
