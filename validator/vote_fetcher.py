"""
Simple HTTP client that fetches the most‑recent vote vector snapshot
from oceans66.com and returns it as a list of `Vote` pydantic models.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict, List, Optional

from api.schemas import Vote                       # pydantic model
from config import settings
from storage.models import VoteSnapshot            # SQLAlchemy ORM
from api.client import VoteAPIClient               
from validator.state_cache import StateCache

log = logging.getLogger("validator.vote_fetcher")

# ──────────────────────────────────────────────────────────────────────
# Thin HTTP wrapper (already supplied earlier)
# ──────────────────────────────────────────────────────────────────────
import requests
from requests import Response, Session


class VoteFetcher:
    """
    • Fetches the most‑recent α‑Stake vote vector
    • Normalises it so Σ weights = 1
    • Exposes it on the shared :class:`StateCache` for the running process
    • Persists *new* `VoteSnapshot` rows for later analytics
    """

    # --------------------------------------------------------------- #
    # Construction
    # --------------------------------------------------------------- #
    def __init__(
        self,
        cache: StateCache,
        *,
        api_client: Optional[VoteAPIClient] = None,   # DI for tests
    ) -> None:
        self.cache = cache
        self._api = api_client or VoteAPIClient()

    # --------------------------------------------------------------- #
    # Main entry‑point (called once per epoch)
    # --------------------------------------------------------------- #
    def fetch_and_store(self) -> Dict[int, float]:
        """
        Returns
        -------
        Dict[int, float]
            Mapping **subnet_id → weight** whose values sum to 1.0.
            The same mapping is also attached to ``self.cache.subnet_weights``.
        """
        votes = self._api.get_latest_votes()
        if not votes:
            log.warning("VoteFetcher received an empty vote list; "
                        "falling back to empty weights")
            self.cache.subnet_weights = {}
            return {}

        # 1️⃣  Aggregate duplicated subnet entries (if any) -------------------
        weights: Dict[int, float] = defaultdict(float)
        for v in votes:
            try:
                weights[int(v.subnet_id)] += float(v.weight)
            except Exception as exc:  # defensive: skip malformed rows
                log.debug("Skipping malformed vote row %r: %s", v, exc)

        # 2️⃣  Normalise so Σ = 1.0  (avoid div‑by‑zero) -----------------------
        total = sum(weights.values())
        if total > 0.0:
            weights = {sid: w / total for sid, w in weights.items()}
        else:
            log.warning("Total α‑Stake weight is zero; "
                        "all subnets will receive zero reward weight")
            weights = {}

        # 3️⃣  Expose to the in‑memory cache for RewardCalculator -------------
        self.cache.subnet_weights = weights

        # 4️⃣  Convert to ORM snapshots and persist *new* rows ----------------
        snapshots: List[VoteSnapshot] = []
        with self.cache._session() as db:  # pylint: disable=protected-access
            for v in votes:
                for sid, w in v.weights.items():
                # De‑duplication: skip if (voter_hotkey, block_height) exists.
                if not self.cache.votes_changed(v.block_height, v.voter_hotkey):
                    continue

                snapshots.append(
                    VoteSnapshot(
                        voter_hotkey=v.voter_hotkey,
                        subnet_id=v.subnet_id,
                        weight=float(v.weight),
                        block_height=v.block_height,
                    )
                )

        if snapshots:
            self.cache.persist_votes(snapshots)
            log.info("Persisted %d new VoteSnapshot rows", len(snapshots))
        else:
            log.debug("No new vote snapshots to persist")

        # Optional convenience attribute for other components
        self.cache.latest_votes = snapshots

        return weights