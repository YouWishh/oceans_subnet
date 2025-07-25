"""
Typed synchronous client for the Oceans vote API.

If `config.settings.VOTE_API_ENDPOINT` is left at its default value
("TODO"), the client operates in **offline mode** and returns a small,
deterministic set of dummy votes so the rest of the codebase keeps
working while the real service is unavailable.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List

import backoff
import httpx

from config import settings
from .schemas import Vote

log = logging.getLogger("vote_api_client")

# ── Hard‑coded dummy data ────────────────────────────────────────────────
_DUMMY_VOTER_HOTKEYS: List[str] = [
    "5HdK1zyMbMoq1NM2sDL2Len9h2CsmBcVbrFthePccMN5R8jU",
    "5CdG8JDyzBPvXD1PM3ctdVmk3DbC52aTmYbQNezasVUXsn66",
    "5CsvRJXuR955WojnGMdok1hbhffZyB4N5ocrv82f3p5A2zVp",
    "5ExiuLNctkEUL5xMijujmAdhJGdzb5d6vxdzLdjpH3MLNovF",
]
_DUMMY_BLOCK_HEIGHT: int = 6073385
_DUMMY_WEIGHTS: Dict[int, float] = {i: 1 / 128 for i in range(1, 129)}
_OFFLINE_SENTINEL = "TODO"


class VoteAPIClient:
    """
    Minimal wrapper around :pyclass:`httpx.Client` with automatic retries.

    • **Online mode**  – `settings.VOTE_API_ENDPOINT` is a real URL; HTTP
      calls are made as usual.

    • **Offline mode** – `settings.VOTE_API_ENDPOINT` is `"TODO"`; the
      client returns deterministic dummy data instead of performing any
      network I/O.
    """

    DEFAULT_TIMEOUT = 10.0

    # ────────────────────────────────────────────────────────
    # Construction & teardown
    # ────────────────────────────────────────────────────────
    def __init__(self, base_url: str | None = None, timeout: float | None = None):
        self.base_url = str(base_url or settings.VOTE_API_ENDPOINT).rstrip("/")
        self.timeout = timeout or self.DEFAULT_TIMEOUT
        self._offline = self.base_url.upper() == _OFFLINE_SENTINEL
        self._client: httpx.Client | None = None

        if self._offline:
            log.warning(
                "VoteAPIClient initialised in **offline** mode – "
                "returning dummy votes until a real endpoint is configured"
            )
        else:
            self._client = httpx.Client(base_url=self.base_url, timeout=self.timeout)
            log.info("VoteAPIClient initialised in online mode → %s", self.base_url)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    # Enable use with the ``with`` statement
    def __enter__(self) -> "VoteAPIClient":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:  # noqa: D401
        self.close()

    # ────────────────────────────────────────────────────────
    # Public API
    # ────────────────────────────────────────────────────────
    @backoff.on_exception(
        backoff.expo, httpx.HTTPError, max_tries=5, jitter=None, factor=2
    )
    def get_latest_votes(self) -> List[Vote]:
        """
        Return the most recent vote‑vector per voter.

        * **Online mode** – GET ``/votes/latest`` from the configured API.
        * **Offline mode** – return hard‑coded votes defined above.
        """
        if self._offline:
            return self._generate_dummy_votes()

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

    # ────────────────────────────────────────────────────────
    # Internal helpers
    # ────────────────────────────────────────────────────────
    @staticmethod
    def _generate_dummy_votes() -> List[Vote]:
        """
        Build a deterministic set of :class:`Vote` objects used in offline mode.
        """
        now = datetime.now(timezone.utc)
        return [
            Vote(
                voter_hotkey=hk,
                block_height=_DUMMY_BLOCK_HEIGHT,
                weights=_DUMMY_WEIGHTS,
                timestamp=now,
            )
            for hk in _DUMMY_VOTER_HOTKEYS
        ]
