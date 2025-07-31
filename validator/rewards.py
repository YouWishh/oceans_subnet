"""
Reward‑calculator v3.1  —  stake‑aware + liquidity‑aware
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Reward(N) = Σ_subnets  ( LP_N,sub / Σ LP_all,sub ) × MasterWeight_sub
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

import bittensor as bt
from validator.state_cache import StateCache


class RewardCalculator:
    """
    Computes the per‑miner reward weights for the current epoch.

    Cache expectations
    ------------------
    • cache.latest_votes : List[VoteSnapshot]
    • cache.liquidity    : Dict[int, Dict[int, float]]
    """

    def __init__(self, cache: StateCache):
        self.cache = cache

    # ------------------------------------------------------------------ #
    # PUBLIC
    # ------------------------------------------------------------------ #
    def compute(self, *, metagraph) -> Dict[int, float]:
        uids: List[int] = list(getattr(metagraph, "uids", []))
        if not uids:
            bt.logging.warning("[RewardCalc] Metagraph contained no UIDs")
            return {}

        # 1️⃣  Master subnet vector --------------------------------------
        master_w = self._build_master_vector()

        # 2️⃣  Liquidity map --------------------------------------------
        liquidity: Dict[int, Dict[object, float]] = getattr(
            self.cache, "liquidity", {}
        )

        # 3️⃣  Apply formula --------------------------------------------
        rewards: Dict[int, float] = defaultdict(float)

        for raw_sid, w_sub in master_w.items():
            try:
                subnet_id = int(raw_sid)
            except Exception:
                bt.logging.debug(
                    f"[RewardCalc] Skipping non‑numeric subnet id “{raw_sid}”"
                )
                continue

            if w_sub <= 0.0:
                continue

            lp_by_key = liquidity.get(subnet_id, {})
            total_lp = sum(lp_by_key.values())

            bt.logging.debug(
                f"[RewardCalc] Subnet {subnet_id}: total LP = {total_lp:.9f}, "
                f"weight = {w_sub:.6f}"
            )

            if total_lp == 0.0:
                continue

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
                if contrib > 0:
                    rewards[uid] += contrib
                    bt.logging.debug(
                        f"[RewardCalc]    uid {uid:<4} +{contrib:.9f}"
                    )

        # 4️⃣  Normalise or fallback ------------------------------------
        ttl = sum(rewards.values())
        if ttl > 0.0:
            norm = 1.0 / ttl
            rewards = {uid: r * norm for uid, r in rewards.items()}
            bt.logging.info(
                f"[RewardCalc] Rewards normalised, {len(rewards)} active miners "
                f"(Σ = {sum(rewards.values()):.6f})"
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
        votes = getattr(self.cache, "latest_votes", [])

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

            s = sum(weights.values())
            if s <= 0.0:
                continue

            for sid, w in weights.items():
                raw[int(sid)] += stake * (float(w) / s)

            total_stake += stake

        if total_stake == 0.0:
            return {}

        master_w = {sid: w / total_stake for sid, w in raw.items()}
        self.cache.master_subnet_weights = master_w
        bt.logging.info(
            f"[RewardCalc] Master subnet vector: {len(master_w)} subnets "
            f"(Σ = {sum(master_w.values()):.6f})"
        )
        return master_w
