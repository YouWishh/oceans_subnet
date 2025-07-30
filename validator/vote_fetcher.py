"""
Simple HTTP client that fetches the most‑recent α‑Stake vote snapshot
from oceans66.com and exposes it to the running validator.

Key responsibilities
--------------------
• Fetch the latest votes via :class:`api.client.VoteAPIClient`
• Aggregate and *normalise* subnet‑level weights so Σ = 1.0
• Store the weights in a shared :class:`StateCache`
• Persist **one** :class:`storage.models.VoteSnapshot` row *per voter*
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict, List, Optional, Tuple, Any

import bittensor as bt                         # ⇐ extra logging via bt.logging
from api.client import VoteAPIClient
from api.schemas import Vote                   # pydantic model
from validator.state_cache import StateCache
from storage.models import VoteSnapshot        # SQLAlchemy ORM

# Retained for dependency parity; the fetcher never calls requests directly.
import requests  # noqa: F401

# --------------------------------------------------------------------------- #
# Module‑level logger (Python stdlib) – keep for libraries relying on it,
# but *all* important messages are duplicated to `bt.logging.info()` so the
# user sees them in the Validator console.
# --------------------------------------------------------------------------- #
log = logging.getLogger(__name__)


class VoteFetcher:
    """
    Entry‑point invoked once per epoch by the validator scheduler.
    """

    # --------------------------------------------------------------------- #
    # Construction
    # --------------------------------------------------------------------- #
    def __init__(
        self,
        cache: StateCache,
        *,
        api_client: Optional[VoteAPIClient] = None,   # injectable for tests
    ) -> None:
        self.cache = cache
        self._api = api_client or VoteAPIClient()

    # --------------------------------------------------------------------- #
    # Public API
    # --------------------------------------------------------------------- #
    def fetch_and_store(self) -> Dict[int, float]:
        """
        Fetch votes → aggregate → normalise → cache → persist.

        Returns
        -------
        Dict[int, float]
            Mapping ``subnet_id → weight`` whose values sum to 1 · 0.
            The same mapping is also available at
            ``self.cache.subnet_weights`` immediately after the call.
        """
        # 1️⃣  Fetch ---------------------------------------------------------
        votes: List[Vote] = self._api.get_latest_votes()
        bt.logging.info(f"[VoteFetcher] Fetched {len(votes)} votes")
        log.info("Fetched %d votes", len(votes))

        if not votes:
            bt.logging.warning("[VoteFetcher] Empty vote list – all weights = 0")
            self.cache.subnet_weights = {}
            return {}

        # 2️⃣  Aggregate raw subnet weights ---------------------------------
        raw_weights: Dict[int, float] = defaultdict(float)
        for v in votes:
            try:
                for sid, w in self._flatten_vote(v):
                    raw_weights[int(sid)] += float(w)
            except Exception as exc:   # defensive: skip malformed rows
                log.debug("Skipping malformed vote row %r: %s", v, exc)
                bt.logging.debug(f"[VoteFetcher] Skipped malformed vote {v}: {exc}")

        total_raw: float = sum(raw_weights.values())
        bt.logging.info(
            f"[VoteFetcher] Aggregated raw weights for {len(raw_weights)} subnets "
            f"(Σ = {total_raw:.6f})"
        )

        # 3️⃣  Normalise so Σ = 1.0 (avoid /0) ------------------------------
        if total_raw > 0.0:
            weights: Dict[int, float] = {
                sid: w / total_raw for sid, w in raw_weights.items()
            }
        else:
            bt.logging.warning(
                "[VoteFetcher] Total α‑Stake weight is zero – "
                "all subnets will receive 0 weight"
            )
            weights = {}

        # Log first few weights for debugging
        _preview: List[Tuple[int, float]] = sorted(weights.items())[:10]
        bt.logging.info(
            "[VoteFetcher] Normalised weights preview (first 10): "
            f"{_preview}"
        )

        # 4️⃣  Expose to the in‑memory cache for RewardCalculator -----------
        self.cache.subnet_weights = weights

        # 5️⃣  Persist **one** VoteSnapshot per *voter* ----------------------
        snapshots: List[VoteSnapshot] = []
        with self.cache._session():                           # pylint: disable=protected-access
            for v in votes:
                # Skip if nothing changed for this (voter, block_height)
                if not self.cache.votes_changed(v.block_height, v.voter_hotkey):
                    continue

                snapshots.append(
                    VoteSnapshot(
                        voter_hotkey=v.voter_hotkey,
                        block_height=v.block_height,
                        weights=v.weights,        # JSON payload {sid: weight, …}
                    )
                )

        if snapshots:
            self.cache.persist_votes(snapshots)
            bt.logging.info(
                f"[VoteFetcher] Persisted {len(snapshots)} new VoteSnapshot rows"
            )
        else:
            bt.logging.debug("[VoteFetcher] No new vote snapshots to persist")

        # Optional convenience attribute for other components
        self.cache.latest_votes = snapshots

        # 6️⃣  Return --------------------------------------------------------
        bt.logging.info(
            "[VoteFetcher] Returning subnet‑weight vector (len = "
            f"{len(weights)}, Σ = {sum(weights.values()):.6f})"
        )
        return weights

    # --------------------------------------------------------------------- #
    # Internal helpers
    # --------------------------------------------------------------------- #
    @staticmethod
    def _flatten_vote(v: Vote) -> List[Tuple[int, float]]:
        """
        Accepts either *row‑style* Vote objects (legacy) or the modern
        *dict‑style* Vote objects (``weights: {sid: w, …}``).

        Always returns ``List[(subnet_id, weight)]`` pairs.
        """
        if getattr(v, "weights", None):
            return [(int(sid), float(w)) for sid, w in v.weights.items()]

        # Legacy shape (rare / deprecated)
        if hasattr(v, "subnet_id") and hasattr(v, "weight"):
            return [(int(v.subnet_id), float(v.weight))]

        bt.logging.debug("[VoteFetcher] Unrecognised Vote payload: %r", v)
        return []
