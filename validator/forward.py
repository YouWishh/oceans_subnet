"""
Validator‑side *business logic* executed once at every epoch head.

This file is deliberately kept free of any bittensor‑specific
subclassing to make unit‑testing easier.

The public coroutine `forward()` is imported and called by
`EpochValidatorNeuron.forward()` in `validator/epoch_validator.py`.
"""

from typing import Dict

import torch
import bittensor as bt


async def forward(neuron) -> torch.FloatTensor:
    """
    Compute and return the normalised weight tensor for the current epoch.

    Parameters
    ----------
    neuron : EpochValidatorNeuron
        The running validator instance.  It exposes:
            • vote_fetcher, liq_fetcher, reward_calc
            • metagraph (with `.uids`)
            • any other attributes you may want to use later.

    Returns
    -------
    torch.FloatTensor
        A 1‑D tensor of length `len(neuron.metagraph.uids)` whose entries
        sum to 1.0.  The order **must** match `metagraph.uids`.
    """
    # 1️⃣  Ingest fresh data ----------------------------------------------------
    neuron.vote_fetcher.fetch_and_store()
    neuron.liq_fetcher.fetch_and_store()

    # 2️⃣  Compute per‑miner weights -------------------------------------------
    uid_weights: Dict[int, float] = neuron.reward_calc.compute(
        metagraph=neuron.metagraph
    )

    # 3️⃣  Convert {uid: w} → tensor in metagraph order -------------------------
    tensor = torch.zeros(len(neuron.metagraph.uids), dtype=torch.float32)
    for uid, weight in uid_weights.items():
        if 0 <= uid < tensor.shape[0]:
            tensor[uid] = float(weight)

    # 4️⃣  Normalise so Σw = 1.0  (fallback = uniform) --------------------------
    total = tensor.sum()
    if total > 0:
        tensor /= total
    else:
        tensor += 1.0 / tensor.numel()

    bt.logging.debug(f"[forward] produced weight vector of shape {tensor.shape}")
    return tensor
