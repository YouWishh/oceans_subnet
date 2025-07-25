"""
Reward‑calculator v1.0
~~~~~~~~~~~~~~~~~~~~~

The economic formula now uses both **α‑Stake subnet weights** and **miner
liquidity (LP) snapshots** cached in :class:`validator.state_cache.StateCache`.

For each *active* miner **N** we calculate

    Reward(N) = Σ_subnets  ( LP_N,subnet / Σ LP_all,subnet ) × Weight_subnet

The resulting dictionary **uid → weight** is normalised so that **Σ weights = 1.0**,
ready for direct use by ``EpochValidatorNeuron.forward()``.
"""

from __future__ import annotations

from typing import Dict

from validator.state_cache import StateCache


class RewardCalculator:
    """
    Computes the per‑miner reward weights for the current Bittensor epoch.

    Parameters
    ----------
    cache :
        A :class:`~validator.state_cache.StateCache` instance that must expose

        * ``subnet_weights`` – *Dict[int, float]*  
          Mapping **subnet_id → α‑Stake weight** (should already sum to 1.0).

        * ``liquidity`` – *Dict[int, Dict[int, float]]*  
          Nested mapping **subnet_id → { uid → lp_amount }** containing the
          latest LP snapshot for every miner on each subnet.
    """

    def __init__(self, cache: StateCache):
        self.cache = cache

    # ------------------------------------------------------------------ #
    # PUBLIC
    # ------------------------------------------------------------------ #
    def compute(self, *, metagraph) -> Dict[int, float]:
        """
        Return a weight for every **active** miner UID found in `metagraph`.

        Notes
        -----
        * Subnets with either **zero total liquidity** or **zero α‑Stake weight**
          are ignored.
        * If no meaningful data are available the function falls back to a
          uniform distribution.
        """
        uids = list(getattr(metagraph, "uids", []))
        if not uids:
            return {}

        subnet_weights: Dict[int, float] = getattr(self.cache, "subnet_weights", {})
        liquidity: Dict[int, Dict[int, float]] = getattr(self.cache, "liquidity", {})

        # Initialise reward vector
        rewards: Dict[int, float] = {int(uid): 0.0 for uid in uids}

        # Σ_subnets  ( LP_uid / Σ LP ) × Weight_subnet
        for subnet_id, weight_sn in subnet_weights.items():
            if weight_sn <= 0.0:
                continue  # subnet currently carries no economic weight

            lp_by_uid = liquidity.get(subnet_id, {})
            total_liq = sum(lp_by_uid.values())
            if not lp_by_uid or total_liq <= 0.0:
                continue  # nothing staked on this subnet

            scale = weight_sn / total_liq
            for uid in uids:
                lp = lp_by_uid.get(int(uid), 0.0)
                if lp:
                    rewards[int(uid)] += lp * scale

        # Normalise so that Σ rewards = 1.0 (required by downstream code)
        total_reward = sum(rewards.values())
        if total_reward > 0:
            rewards = {uid: r / total_reward for uid, r in rewards.items()}
        else:  # graceful fallback → uniform distribution
            uniform = 1.0 / len(uids)
            rewards = {int(uid): uniform for uid in uids}

        return rewards
