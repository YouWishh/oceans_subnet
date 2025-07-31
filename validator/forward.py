"""
Validator‑side *business logic* executed once at every epoch head.
"""
from typing import Dict

import numpy as np
import bittensor as bt


async def forward(neuron) -> None:
    """
    Compute miner‑score vector for the current epoch and store it on the
    running validator (`neuron`) via `neuron.update_scores()`.
    """
    # 1️⃣  Ingest fresh data ------------------------------------------------
    bt.logging.info("[forward] Fetching latest on‑chain and off‑chain data…")
    neuron.vote_fetcher.fetch_and_store()             # synchronous
    await neuron.liq_fetcher.fetch_and_store()        # asynchronous – await

    # 2️⃣  Compute per‑miner raw scores ------------------------------------
    bt.logging.info("[forward] Computing raw miner scores…")
    uid_scores: Dict[int, float] = neuron.reward_calc.compute(
        metagraph=neuron.metagraph
    )

    # 3️⃣  Convert {uid: score} → NumPy arrays in metagraph order ----------
    num_uids = len(neuron.metagraph.uids)
    boosted = np.zeros(num_uids, dtype=np.float32)
    uids_np = np.asarray(neuron.metagraph.uids, dtype=np.int64)

    for uid, score in uid_scores.items():
        if 0 <= uid < num_uids:
            boosted[uid] = float(score)

    # 4️⃣  Normalise so Σ = 1.0 (fallback = uniform) -----------------------
    total = float(boosted.sum(dtype=np.float32))
    if total > 0.0:
        boosted /= total
    else:
        boosted.fill(1.0 / num_uids)

    bt.logging.info(
        f"[forward] Normalised scores for {num_uids} miners (Σ = {boosted.sum():.6f})."
    )

    # 5️⃣  Persist the scores on the neuron object -------------------------
    bt.logging.info(f"[forward] Updating neuron's score table…")
    bt.logging.debug(f"[forward] {boosted=}, {uids_np=}")
    neuron.update_scores(boosted, uids_np)
