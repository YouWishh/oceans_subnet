"""
Simple HTTP client that fetches the most‑recent α‑Stake vote snapshots
and exposes them to the running validator.

Responsibilities
----------------
• Fetch votes via :class:`api.client.VoteAPIClient`
• Aggregate *stake‑weighted* subnet weights so Σ = 1.0
• Store the weights in a shared :class:`StateCache`
• Persist **one** :class:`storage.models.VoteSnapshot` row *per voter*
• Return an *array* of ``(voter_stake, weights)`` pairs for further use
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import bittensor as bt

from api.client import VoteAPIClient
from api.schemas import Vote
from storage.models import VoteSnapshot
from validator.state_cache import StateCache

# Retained for dependency parity; the fetcher never calls requests directly.
import requests  # noqa: F401

# --------------------------------------------------------------------------- #
# Module‑level logger (Python stdlib) – still useful for tests, but all key
# messages are echoed to `bt.logging.*` for on‑chain validator logs.
# --------------------------------------------------------------------------- #
log = logging.getLogger(__name__)


class VoteFetcher:
    """
    Entry‑point invoked once per epoch by the validator scheduler.
    """

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    def __init__(
        self,
        cache: StateCache,
        *,
        api_client: Optional[VoteAPIClient] = None,  # injectable for tests
    ) -> None:
        self.cache = cache
        self._api = api_client or VoteAPIClient()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def fetch_and_store(self) -> List[Tuple[float, Dict[int, float]]]:
        """
        Fetch → aggregate → normalise → cache → persist.

        Returns
        -------
        List[Tuple[float, Dict[int, float]]]
            One tuple *per voter*:

            ``(voter_stake, weights_dict)``, where ``weights_dict`` is the
            original JSON mapping **without normalisation** (exactly what
            the voter submitted).
        """
        # 1️⃣  Fetch ---------------------------------------------------------
        votes: List[Vote] = self._api.get_latest_votes()
        bt.logging.info(f"[VoteFetcher] Fetched {len(votes)} votes")
        log.info("Fetched %d votes", len(votes))

        if not votes:
            bt.logging.warning("[VoteFetcher] Empty vote list – all weights = 0")
            self.cache.subnet_weights = {}
            return []

        # 2️⃣  Aggregate *stake‑weighted* subnet weights --------------------
        raw_weights: Dict[int, float] = defaultdict(float)
        for v in votes:
            stake = float(v.voter_stake)
            for sid, w in v.weights.items():
                raw_weights[int(sid)] += float(w) * stake

        total_weight: float = sum(raw_weights.values())
        bt.logging.info(
            f"[VoteFetcher] Aggregated stake‑weighted weights for "
            f"{len(raw_weights)} subnets (Σ = {total_weight:.6f})"
        )

        # 3️⃣  Normalise so Σ = 1.0 (if possible) ---------------------------
        if total_weight > 0.0:
            norm_weights: Dict[int, float] = {
                sid: w / total_weight for sid, w in raw_weights.items()
            }
        else:
            bt.logging.warning(
                "[VoteFetcher] Total stake‑weighted mass is zero – "
                "all subnets will receive 0 reward weight"
            )
            norm_weights = {}

        # 4️⃣  Store on shared cache for RewardCalculator -------------------
        self.cache.subnet_weights = norm_weights

        # 5️⃣  Persist **one** snapshot per voter ---------------------------
        snapshots: List[VoteSnapshot] = []
        with self.cache._session():  # pylint: disable=protected-access
            for v in votes:
                if not self.cache.votes_changed(v.block_height, v.voter_hotkey):
                    continue

                snapshots.append(
                    VoteSnapshot(
                        voter_hotkey=v.voter_hotkey,
                        block_height=v.block_height,
                        voter_stake=v.voter_stake,
                        weights=v.weights,  # JSON {sid: w, …}
                    )
                )

        if snapshots:
            self.cache.persist_votes(snapshots)
            bt.logging.info(
                f"[VoteFetcher] Persisted {len(snapshots)} new VoteSnapshot rows"
            )
        else:
            bt.logging.debug("[VoteFetcher] No new vote snapshots to persist")

        # 6️⃣  Debug preview -------------------------------------------------
        preview = [
            (v.voter_hotkey[:6] + "…", v.voter_stake, list(v.weights.items())[:3])
            for v in votes[:5]
        ]
        bt.logging.info(
            "[VoteFetcher] First 5 voters preview (hotkey‑truncated): %s", preview
        )

        # 7️⃣  Return (stake, weights) pairs ---------------------------------
        result: List[Tuple[float, Dict[int, float]]] = [
            (float(v.voter_stake), dict(v.weights)) for v in votes
        ]
        bt.logging.info(
            f"[VoteFetcher] Returning {len(result)} (stake, weights) tuples"
        )
        return result
