"""
Simple HTTP client that fetches the most‑recent vote‑vector snapshot
from oceans66.com and exposes it to the running validator.

Responsibilities
----------------
• Fetch the latest votes via :class:`api.client.VoteAPIClient`
• Aggregate and *normalise* subnet‑level weights so Σ = 1.0
• Make the weights available on a shared :class:`StateCache`
• Persist new :class:`storage.models.VoteSnapshot` rows for analytics
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from api.client import VoteAPIClient
from api.schemas import Vote                       # pydantic model
from validator.state_cache import StateCache
from storage.models import VoteSnapshot            # SQLAlchemy ORM

log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────
# Optional direct `requests` import retained for dependency paritity.
# (VoteFetcher itself never calls the low‑level HTTP API.)
# ──────────────────────────────────────────────────────────────────────
import requests                                     # noqa: F401


class VoteFetcher:
    """
    Entry‑point called once per epoch by the validator scheduler.
    """

    # ----------------------------------------------------------------- #
    # Construction
    # ----------------------------------------------------------------- #
    def __init__(
        self,
        cache: StateCache,
        *,
        api_client: Optional[VoteAPIClient] = None,   # injectable for tests
    ) -> None:
        self.cache = cache
        self._api = api_client or VoteAPIClient()

    # ----------------------------------------------------------------- #
    # Public API
    # ----------------------------------------------------------------- #
    def fetch_and_store(self) -> Dict[int, float]:
        """
        Fetch votes → aggregate → normalise → cache → persist.

        Returns
        -------
        Dict[int, float]
            Mapping ``subnet_id → weight`` whose values sum (approximately)
            to 1 · 0.  The same mapping is also available at
            ``self.cache.subnet_weights`` immediately after the call.
        """
        votes: List[Vote] = self._api.get_latest_votes()
        if not votes:
            log.warning(
                "VoteFetcher received an empty vote list; "
                "falling back to empty weights",
            )
            self.cache.subnet_weights = {}
            return {}

        # 1️⃣  Aggregate raw subnet weights ----------------------------------
        raw_weights: Dict[int, float] = defaultdict(float)
        for v in votes:
            try:
                for sid, w in self._flatten_vote(v):
                    raw_weights[int(sid)] += float(w)
            except Exception as exc:   # defensive: skip malformed rows
                log.debug("Skipping malformed vote row %r: %s", v, exc)

        # 2️⃣  Normalise so Σ = 1.0  (avoid /0) ------------------------------
        total = sum(raw_weights.values())
        if total > 0.0:
            weights: Dict[int, float] = {
                sid: w / total for sid, w in raw_weights.items()
            }
        else:
            log.warning(
                "Total α‑Stake weight is zero; "
                "all subnets will receive zero reward weight",
            )
            weights = {}

        # 3️⃣  Expose to the in‑memory cache for RewardCalculator ------------
        self.cache.subnet_weights = weights

        # 4️⃣  Persist new VoteSnapshot rows ---------------------------------
        snapshots: List[VoteSnapshot] = []
        with self.cache._session():                             # pylint: disable=protected-access
            for v in votes:
                if not self.cache.votes_changed(v.block_height, v.voter_hotkey):
                    continue

                for sid, w in self._flatten_vote(v):
                    snapshots.append(
                        VoteSnapshot(
                            voter_hotkey=v.voter_hotkey,
                            subnet_id=int(sid),
                            weight=float(w),
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

    # ----------------------------------------------------------------- #
    # Internal helpers
    # ----------------------------------------------------------------- #
    @staticmethod
    def _flatten_vote(v: Vote) -> List[Tuple[int, float]]:
        """
        Accepts either *row‑style* Vote objects (``subnet_id``, ``weight``)
        or *dict‑style* Vote objects (``weights: {sid: w, …}``).

        Always returns ``List[(subnet_id, weight)]`` pairs.
        """
        # The oceans66 API historically served one row per subnet, but the
        # newer schema supplies a weight vector.  Support both.
        if getattr(v, "weights", None):
            return [(int(sid), float(w)) for sid, w in v.weights.items()]

        if hasattr(v, "subnet_id") and hasattr(v, "weight"):
            return [(int(v.subnet_id), float(v.weight))]

        log.debug("Unrecognised Vote payload shape: %r", v)
        return []
