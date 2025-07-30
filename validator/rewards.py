"""
Reward‑calculator v2 — stake‑aware
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The economic formula now derives a **master subnet‑weight vector** that is
*explicitly weighted by each voter’s α‑stake* and then combines that with
miners’ liquidity snapshots.

For every active miner **N**:

    Reward(N) = Σ_subnets  ( LP_N,sub / Σ LP_all,sub ) × MasterWeight_sub

where

    MasterWeight_sub  = Σ_voters ( voter_stake × voter_weight_sub ) / Σ_voter_stake

The resulting mapping **uid → reward** is normalised so that Σ = 1.0 and can
be pushed directly on‑chain.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

import bittensor as bt
from validator.state_cache import StateCache


class RewardCalculator:
    """
    Computes the per‑miner reward weights for the current Bittensor epoch.

    Expected `StateCache` attributes
    --------------------------------
    • `latest_votes` – *List[VoteSnapshot]* \
      Each snapshot **must** expose `.voter_stake` (float) and \
      `.weights` (dict {subnet_id: weight})

    • `liquidity` – *Dict[int, Dict[int, float]]* \
      Nested mapping **subnet_id → { uid → lp_amount }**
    """

    def __init__(self, cache: StateCache):
        self.cache = cache

    # ------------------------------------------------------------------ #
    # PUBLIC
    # ------------------------------------------------------------------ #
    def compute(self, *, metagraph) -> Dict[int, float]:
        """
        Return one reward weight for every UID in `metagraph.uids`.
        """
        uids: List[int] = list(getattr(metagraph, "uids", []))
        if not uids:
            return {}

        # 1️⃣  Build stake‑weighted master subnet vector -------------------
        master_w = self._build_master_vector()
        if not master_w:
            bt.logging.warning(
                "[RewardCalc] Could not build master subnet vector – "
                "falling back to uniform subnet weights"
            )

        # 2️⃣  Obtain liquidity snapshots ---------------------------------
        liquidity: Dict[int, Dict[int, float]] = getattr(self.cache, "liquidity", {})

        # 3️⃣  Compute miner rewards --------------------------------------
        rewards: Dict[int, float] = {int(uid): 0.0 for uid in uids}

        for subnet_id, w_sn in master_w.items():
            if w_sn <= 0.0:
                continue

            lp_by_uid = liquidity.get(subnet_id, {})
            total_lp = sum(lp_by_uid.values())
            if total_lp <= 0.0:
                continue  # no liquidity on this subnet

            factor = w_sn / total_lp
            for uid in uids:
                lp = lp_by_uid.get(int(uid), 0.0)
                if lp:
                    rewards[int(uid)] += lp * factor

        # 4️⃣  Normalise (Σ = 1) or uniform fallback ----------------------
        total_reward = sum(rewards.values())
        if total_reward > 0:
            rewards = {uid: r / total_reward for uid, r in rewards.items()}
        else:
            bt.logging.warning(
                "[RewardCalc] Reward vector zeroed – using uniform distribution"
            )
            uniform = 1.0 / len(uids)
            rewards = {int(uid): uniform for uid in uids}

        return rewards

    # ------------------------------------------------------------------ #
    # INTERNAL
    # ------------------------------------------------------------------ #
    def _build_master_vector(self) -> Dict[int, float]:
        """
        Create the stake‑weighted subnet vector and store it on the cache
        for debugging / downstream use.
        """
        votes = getattr(self.cache, "latest_votes", [])  # List[VoteSnapshot]

        # Fallback to previous pipeline if votes are unavailable
        if not votes:
            return getattr(self.cache, "subnet_weights", {})

        raw: Dict[int, float] = defaultdict(float)
        total_stake: float = 0.0

        for vs in votes:
            stake = float(getattr(vs, "voter_stake", 0.0))
            weights = getattr(vs, "weights", {}) or {}
            if stake <= 0.0 or not weights:
                continue

            # Ensure each voter's weights sum to 1.0 (defensive)
            s = sum(weights.values())
            if s <= 0.0:
                continue

            for sid, w in weights.items():
                raw[int(sid)] += stake * (float(w) / s)

            total_stake += stake

        if total_stake <= 0.0:
            return {}

        master_w = {sid: w / total_stake for sid, w in raw.items()}

        # Persist for visibility
        self.cache.master_subnet_weights = master_w
        bt.logging.info(
            f"[RewardCalc] Master subnet vector built for {len(master_w)} subnets "
            f"(Σ = {sum(master_w.values()):.6f})"
        )
        return master_w
