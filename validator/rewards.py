"""
Reward‑calculator v3  —  stake‑aware + liquidity‑aware
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Implements

    Reward(N) = Σ_subnets  ( LP_N,sub / Σ LP_all,sub ) × MasterWeight_sub

where

    MasterWeight_sub = Σ_voters ( voter_stake × voter_weight_sub ) / Σ_voter_stake

The mapping **uid → reward** is normalised so that Σ = 1.0 and can be
pushed directly on‑chain.
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
    • `latest_votes` – *List[VoteSnapshot]*  
      Each snapshot **must** expose `.voter_stake` (float) and
      `.weights` (dict {subnet_id: weight})

    • `liquidity` – *Dict[int, Dict[Any, float]]*  
      Nested mapping **subnet_id → { uid_or_key → lp_amount }**

      The inner keys may be ints (UIDs) *or* strings.  They are coerced to
      `int` where possible; non‑convertible keys are ignored.
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
            bt.logging.warning("[RewardCalc] Metagraph contained no UIDs")
            return {}

        # 1️⃣  Build stake‑weighted master subnet vector -----------------
        master_w = self._build_master_vector()

        if not master_w:
            bt.logging.warning(
                "[RewardCalc] Empty master‑weight vector – "
                "falling back to uniform subnet weights"
            )

        # 2️⃣  Liquidity snapshots --------------------------------------
        liquidity: Dict[int, Dict[object, float]] = getattr(
            self.cache, "liquidity", {}
        )

        # 3️⃣  Compute rewards exactly as per formula -------------------
        rewards: Dict[int, float] = defaultdict(float)

        for subnet_id, w_sub in master_w.items():
            if w_sub <= 0.0:
                bt.logging.debug(
                    f"[RewardCalc] Subnet {subnet_id}: master weight <=0 – skipped"
                )
                continue

            lp_by_key = liquidity.get(subnet_id, {})
            total_lp = sum(lp_by_key.values())

            bt.logging.debug(
                f"[RewardCalc] Subnet {subnet_id}: total LP = {total_lp:.6f}, "
                f"weight = {w_sub:.6f}"
            )

            if total_lp <= 0.0:
                continue  # nothing staked on this subnet

            inv_total_lp = 1.0 / total_lp
            for key, lp_amt in lp_by_key.items():
                try:
                    uid = int(key)
                except Exception:
                    bt.logging.debug(
                        f"[RewardCalc]    Ignoring non‑UID key “{key}” "
                        f"for subnet {subnet_id}"
                    )
                    continue

                contrib = lp_amt * inv_total_lp * w_sub
                if contrib > 0.0:
                    rewards[uid] += contrib
                    bt.logging.debug(
                        f"[RewardCalc]    uid {uid:<4} +{contrib:.6f}"
                    )

        # 4️⃣  Normalise (Σ = 1) or uniform fallback --------------------
        total_reward = sum(rewards.values())
        if total_reward > 0:
            norm = 1.0 / total_reward
            rewards = {uid: r * norm for uid, r in rewards.items()}
            bt.logging.info(
                f"[RewardCalc] Rewards normalised (Σ = {sum(rewards.values()):.6f}, "
                f"{len(rewards)} active miners)"
            )
        else:
            bt.logging.warning(
                "[RewardCalc] Reward vector zero – using uniform distribution"
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

        # Fallback: use any cached “subnet_weights” if votes are unavailable
        if not votes:
            cached = getattr(self.cache, "subnet_weights", {})
            if cached:
                bt.logging.info(
                    "[RewardCalc] Using cached subnet_weights (no fresh votes)"
                )
            return cached

        raw: Dict[int, float] = defaultdict(float)
        total_stake: float = 0.0

        for vs in votes:
            stake = float(getattr(vs, "voter_stake", 0.0))
            weights = getattr(vs, "weights", {}) or {}
            if stake <= 0.0 or not weights:
                continue

            # Defensive: ensure each voter’s weights sum to 1.0
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
            f"[RewardCalc] Master subnet vector: {len(master_w)} subnets "
            f"(Σ = {sum(master_w.values()):.6f})"
        )
        return master_w
