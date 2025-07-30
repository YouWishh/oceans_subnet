"""
Validator‑side *business logic* executed once at every epoch head.

This file is deliberately kept free of any bittensor‑specific subclassing
to make unit‑testing easier.

The public coroutine `forward()` is imported and called by
`EpochValidatorNeuron.forward()` in `validator/epoch_validator.py`.
"""

from typing import Dict

import numpy as np
import bittensor as bt


async def forward(neuron) -> None:
    """
    Compute miner‑score vector for the current epoch and store it on the
    running validator (`neuron`) via `neuron.update_scores()`.

    Parameters
    ----------
    neuron : EpochValidatorNeuron
        The running validator instance.  It exposes:
            • vote_fetcher, liq_fetcher, reward_calc
            • metagraph (with `.uids`)
            • update_scores(boosted: np.ndarray, uids_np: np.ndarray)
    """
    # 1️⃣  Ingest fresh data ----------------------------------------------------
    bt.logging.info("[forward] Fetching latest on‑chain and off‑chain data…")
    neuron.vote_fetcher.fetch_and_store()
    neuron.liq_fetcher.fetch_and_store()

    # 2️⃣  Compute per‑miner raw scores ----------------------------------------
    bt.logging.info("[forward] Computing raw miner scores…")
    uid_scores: Dict[int, float] = neuron.reward_calc.compute(
        metagraph=neuron.metagraph
    )

    # 3️⃣  Convert {uid: score} → numpy arrays in metagraph order --------------
    num_uids: int = len(neuron.metagraph.uids)
    boosted       = np.zeros(num_uids, dtype=np.float32)
    uids_np       = np.asarray(neuron.metagraph.uids, dtype=np.int64)

    for uid, score in uid_scores.items():
        if 0 <= uid < num_uids:
            boosted[uid] = float(score)

    # 4️⃣  Normalise so Σscores = 1.0  (fallback = uniform) --------------------
    total: float = float(boosted.sum(dtype=np.float32))
    if total > 0.0:
        boosted /= total
    else:
        boosted.fill(1.0 / num_uids)

    bt.logging.info(
        f"[forward] Normalised scores for {num_uids} miners (Σ = {boosted.sum():.6f})."
    )

    # 5️⃣  Persist the scores on the neuron object -----------------------------
    bt.logging.info("[forward] Updating neuron's score table…")
    neuron.update_scores(boosted, uids_np)
