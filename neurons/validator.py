"""
Epoch‑aware validator for **Subnet 66 – Oceans**.

Key features
------------
• At every new epoch:
    1.  Fetches *latest* votes (off‑chain) & liquidity (on‑chain)
    2.  Persists snapshots via `StateCache`
    3.  Calculates miner‑weights with `RewardCalculator`
    4.  Pushes the weight vector on‑chain (`subtensor.set_weights`)
"""

import asyncio
import traceback
from datetime import datetime
from typing import Optional, Tuple

import bittensor as bt
import torch
from bittensor import BLOCKTIME

from base.validator import BaseValidatorNeuron

from config import settings
from validator.state_cache import StateCache
from validator.vote_fetcher import VoteFetcher
from validator.liquidity_fetcher import LiquidityFetcher
from validator.rewards import RewardCalculator      
from validator.forward import forward               


# ──────────────────────────────────────────────────────────────────────
# Epoch‑aware mix‑in
# ──────────────────────────────────────────────────────────────────────
class EpochValidatorNeuron(BaseValidatorNeuron):
    """
    Subclass of the project’s *BaseValidatorNeuron* that executes custom
    validator logic exactly once per epoch ­— at the first block.
    """

    # ---------------------------------------------------- #
    # Construction
    # ---------------------------------------------------- #
    def __init__(self, *args, log_interval_blocks: int = 2, **kwargs):
        super().__init__(*args, **kwargs)

        # ––– Epoch helpers
        self._log_interval_blocks = max(1, int(log_interval_blocks))
        self._epoch_len: Optional[int] = None
        self.epoch_end_block: Optional[int] = None

        # ––– Oceans components
        self.cache = StateCache()
        self.vote_fetcher = VoteFetcher(self.cache)
        self.liq_fetcher = LiquidityFetcher(self.cache)
        self.reward_calc = RewardCalculator(self.cache)

    # ---------------------------------------------------- #
    # Epoch length detection
    # ---------------------------------------------------- #
    def _discover_epoch_length(self) -> int:
        tempo = self.subtensor.tempo(self.config.netuid) or 360
        try:
            head = self.subtensor.get_current_block()
            next_head = self.subtensor.get_next_epoch_start_block(self.config.netuid)
            if next_head is None:
                raise ValueError("RPC returned None")

            derived = next_head - (head - head % tempo)
            length = derived if derived in (tempo, tempo + 1) else tempo + 1
        except Exception as e:
            bt.logging.warning(f"[epoch] probe error: {e}")
            length = tempo + 1

        if self._epoch_len != length:
            bt.logging.info(f"[epoch] length = {length}")
        self._epoch_len = length
        return length

    def _epoch_snapshot(self) -> Tuple[int, int, int, int, int]:
        blk = self.subtensor.get_current_block()
        ep_len = self._epoch_len or self._discover_epoch_length()
        start = blk - (blk % ep_len)
        end = start + ep_len - 1
        idx = blk // ep_len
        self.epoch_end_block = end
        return blk, start, end, idx, ep_len

    # ---------------------------------------------------- #
    # Waiter: sleep until **first** block of next epoch
    # ---------------------------------------------------- #
    async def _wait_for_next_head(self):
        head_block = self.subtensor.get_current_block()
        ep_len = self._epoch_len or self._discover_epoch_length()
        target_head = head_block - (head_block % ep_len) + ep_len

        while not self.should_exit:
            blk = self.subtensor.get_current_block()
            if blk >= target_head:
                return
            remain = target_head - blk
            eta_s = remain * BLOCKTIME
            bt.logging.info(
                f"[status] Block {blk:,} | {remain} blocks → next epoch "
                f"(~{eta_s // 60:.0f} m {eta_s % 60:02.0f} s)"
            )
            await asyncio.sleep(max(1, min(30, remain // 2)) * BLOCKTIME * 0.95)

    # ---------------------------------------------------- #
    # **LOGIC** – runs once each epoch
    # ---------------------------------------------------- #
    async def forward(self):
        return await forward(self)  

    # ---------------------------------------------------- #
    # Main loop (patched)
    # ---------------------------------------------------- #
    def run(self):  # noqa: D401
        bt.logging.info(
            f"EpochValidator starting at block {self.block:,} (netuid {self.config.netuid})"
        )

        async def _loop():
            while not self.should_exit:
                blk, start, end, idx, ep_len = self._epoch_snapshot()

                # ── Bootstrap: run forward immediately on first loop ── #
                if not getattr(self, "_bootstrapped", False):
                    self.epoch_start_block = start
                    self.epoch_end_block = end
                    self.epoch_index = idx
                    self.epoch_tempo = ep_len

                    bt.logging.info("[bootstrap] running forward immediately")
                    try:
                        self.sync()
                        await self.concurrent_forward()
                    except Exception as e:
                        bt.logging.error(f"bootstrap forward failed: {e}")

                    self._bootstrapped = True

                # Status banner every few blocks ----------------------- #d
                next_head = start + ep_len
                into = blk - start
                left = max(1, next_head - blk)
                eta_s = left * BLOCKTIME
                if into % self._log_interval_blocks == 0:
                    bt.logging.info(
                        f"[status] Block {blk:,} | Epoch {idx} "
                        f"[{into}/{ep_len}] – next epoch in {left} "
                        f"blocks (~{eta_s // 60:.0f} m {eta_s % 60:02.0f} s)"
                    )

                # Wait until epoch rollover --------------------------- #
                await self._wait_for_next_head()

                # ── New epoch head ─────────────────────────────────── #
                self._epoch_len = None  # force re‑probe
                blk2, start2, end2, idx2, ep_len2 = self._epoch_snapshot()
                head_time = datetime.utcnow().strftime("%H:%M:%S")

                self.epoch_start_block = start2
                self.epoch_end_block = end2
                self.epoch_index = idx2
                self.epoch_tempo = ep_len2

                bt.logging.success(
                    f"[epoch {idx2}] head at block {blk2:,} "
                    f"({head_time} UTC) – len={ep_len2}"
                )

                # *** business logic ********************************** #
                try:
                    self.sync()
                    await self.concurrent_forward()
                except Exception as err:
                    bt.logging.error(f"forward() raised: {err}")
                    bt.logging.debug("".join(traceback.format_exception(err)))
                finally:
                    try:
                        self.sync()
                    except Exception as e:
                        bt.logging.warning(f"wallet sync failed: {e}")
                    self.step += 1

        try:
            self.loop.run_until_complete(_loop())
        except KeyboardInterrupt:
            getattr(self, "axon", bt.logging).stop()
            bt.logging.success("Validator stopped by keyboard interrupt.")
