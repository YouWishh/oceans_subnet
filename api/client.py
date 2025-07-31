"""
Typed synchronous client for the Oceans vote API.

If ``config.settings.VOTE_API_ENDPOINT`` is left at its default value
("TODO"), the client operates in **offline mode** and returns deterministic
*temporal* votes so the rest of the codebase keeps working.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Set

import backoff
import httpx

from config import settings
from .schemas import Vote

log = logging.getLogger("vote_api_client")

# ── Hard‑coded temporal data ─────────────────────────────────────────────
_TEMPORAL_VOTER_HOTKEYS: List[str] = [
    "5HdK1zyMbMoq1NM2sDL2Len9h2CsmBcVbrFthePccMN5R8jU",
    "5CdG8JDyzBPvXD1PM3ctdVmk3DbC52aTmYbQNezasVUXsn66",
    "5CsvRJXuR955WojnGMdok1hbhffZyB4N5ocrv82f3p5A2zVp",
    "5ExiuLNctkEUL5xMijujmAdhJGdzb5d6vxdzLdjpH3MLNovF",
]

_TEMPORAL_BLOCK_HEIGHT: int = 6_073_385  # deterministic height
_TEMPORAL_STAKE: float = 1.0             # every temporal voter gets 1 α

# Inactive / unknown subnets (kept for reference – currently unused)
_INACTIVE_SUBNETS: Set[int] = {
    15, 46, 67, 69, 74, 78, 82, 83, 95, 100,
    101, 104, 110, 112, 115, 116, 117, 118, 119, 120,
}

# Subnets that currently have user‑supplied liquidity
ENABLED_USER_LIQUIDITY: Set[int] = {10,27,36,51,73,85,87,97,102,104,106}

# ── NEW LOGIC ────────────────────────────────────────────────────────────
# Active subnets are **only** those with user liquidity.  The legacy
# _INACTIVE_SUBNETS list is left untouched for now in case it is needed
# elsewhere later.
ACTIVE_SUBNETS: List[int] = sorted(ENABLED_USER_LIQUIDITY)

if not ACTIVE_SUBNETS:
    raise ValueError(
        "ENABLED_USER_LIQUIDITY is empty – at least one subnet must be enabled."
    )

_SUBNET_WEIGHT: float = 1 / len(ACTIVE_SUBNETS)  # equal share across active subnets
_TEMPORAL_WEIGHTS: Dict[int, float] = {i: _SUBNET_WEIGHT for i in ACTIVE_SUBNETS}

_OFFLINE_SENTINEL = "TODO"


class VoteAPIClient:
    """
    Wrapper around :class:`httpx.Client` with automatic retries and
    deterministic offline mode.
    """

    DEFAULT_TIMEOUT = 10.0

    # ------------------------------------------------------------------
    # Construction & teardown
    # ------------------------------------------------------------------
    def __init__(self, base_url: str | None = None, timeout: float | None = None):
        self.base_url = str(base_url or settings.VOTE_API_ENDPOINT).rstrip("/")
        self.timeout = timeout or self.DEFAULT_TIMEOUT
        self._offline = self.base_url.upper() == _OFFLINE_SENTINEL
        self._client: httpx.Client | None = None

        if self._offline:
            log.warning(
                "VoteAPIClient initialised in **offline** mode – "
                "returning *temporal* votes until a real endpoint is configured"
            )
        else:
            self._client = httpx.Client(base_url=self.base_url, timeout=self.timeout)
            log.info("VoteAPIClient initialised in online mode → %s", self.base_url)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    # Enable use with the `with` statement
    def __enter__(self) -> "VoteAPIClient":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:  # noqa: D401
        self.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @backoff.on_exception(
        backoff.expo, httpx.HTTPError, max_tries=5, jitter=None, factor=2
    )
    def get_latest_votes(self) -> List[Vote]:
        """
        Return the most recent vote‑vector per voter.

        * **Online mode** – ``GET /votes/latest`` from the configured API.
        * **Offline mode** – return deterministic *temporal* votes.
        """
        if self._offline:
            return self._generate_temporal_votes()

        assert (
            self._client is not None
        ), "httpx.Client should have been initialised in online mode"

        response = self._client.get("/votes/latest")
        response.raise_for_status()

        data = response.json()
        if not isinstance(data, list):
            raise ValueError("Expected JSON list from /votes/latest")

        votes = [Vote(**item) for item in data]
        log.debug("Fetched %d votes from API", len(votes))
        return votes

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _generate_temporal_votes() -> List[Vote]:
        """
        Build a deterministic set of :class:`Vote` objects used in
        offline mode.
        """
        now = datetime.now(timezone.utc)
        return [
            Vote(
                voter_hotkey=hk,
                block_height=_TEMPORAL_BLOCK_HEIGHT,
                voter_stake=_TEMPORAL_STAKE,
                weights=_TEMPORAL_WEIGHTS,
                timestamp=now,
            )
            for hk in _TEMPORAL_VOTER_HOTKEYS
        ]
