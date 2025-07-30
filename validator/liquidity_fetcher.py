"""
Validator‑side helper that pulls liquidity data from chain, converts it
into storage‑layer snapshots and persists only *new* records.

The public method `fetch_and_store()` is now **async**, so it can be awaited
inside the validator’s event‑loop without spawning a nested loop.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Callable, Dict, List, Optional, Tuple, Awaitable

from bittensor import AsyncSubtensor  # type: ignore
from sqlalchemy.orm import Session

from config import settings
from storage.models import LiquiditySnapshot
from validator.state_cache import StateCache
from utils.liquidity_utils import (
    LiquiditySubnet,
    fetch_subnet_liquidity_positions,
)

log = logging.getLogger("validator.liquidity_fetcher")


class LiquidityFetcher:
    """
    Wraps the *async* helpers in `utils.liquidity_utils` with an async,
    validator‑friendly façade and transparent de‑duplication.
    """

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    def __init__(
        self,
        cache: StateCache,
        *,
        fetch_fn: Optional[
            Callable[..., Awaitable[List[LiquiditySubnet]]]
        ] = None,  # injectable for unit‑tests
    ) -> None:
        self.cache = cache
        # If no custom function supplied, use the default async implementation
        self._fetch_fn = fetch_fn or self._default_fetch

    # ------------------------------------------------------------------ #
    # PUBLIC ASYNC ENTRY‑POINT                                           #
    # ------------------------------------------------------------------ #
    async def fetch_and_store(
        self,
        *,
        netuid: Optional[int] = None,
        block: Optional[int] = None,
    ) -> List[LiquiditySnapshot]:
        """
        • Collect liquidity from chain (or injected stub)  
        • Aggregate value per (wallet, subnet)  
        • Store only new (wallet, subnet, block) combinations  
        • Return the list of *persisted* snapshots
        """
        # 1️⃣  Fetch -------------------------------------------------------
        if asyncio.iscoroutinefunction(self._fetch_fn):
            liquidity_subnets = await self._fetch_fn(netuid=netuid, block=block)
        else:
            # Synchronous stub – run in background thread so we don’t block
            liquidity_subnets = await asyncio.to_thread(
                self._fetch_fn, netuid=netuid, block=block
            )

        # 2️⃣  Flatten & aggregate USD value per wallet/subnet ------------
        aggregated: Dict[Tuple[str, int, int], float] = {}
        for ls in liquidity_subnets:
            for coldkey, positions in ls.coldkey_positions.items():
                usd_total = sum(
                    getattr(p, "usd_value", 0.0)
                    or getattr(p, "usd", 0.0)
                    or getattr(p, "value", 0.0)
                    for p in positions
                )
                key = (coldkey, ls.netuid, block or 0)
                aggregated[key] = usd_total

        # 3️⃣  Convert to LiquiditySnapshot objects (dedup via DB) ---------
        new_snapshots: List[LiquiditySnapshot] = []
        with self.cache._session() as db:  # pylint: disable=protected-access
            for (ck, subnet, blk), usd_val in aggregated.items():
                if usd_val == 0.0:
                    continue
                if not self._exists(db, ck, subnet, blk):
                    new_snapshots.append(
                        LiquiditySnapshot(
                            wallet_hotkey=ck,
                            subnet_id=subnet,
                            usd_value=usd_val,
                            block_height=blk,
                        )
                    )

        if new_snapshots:
            self.cache.persist_liquidity(new_snapshots)

        log.info("LiquidityFetcher stored %d new snapshots", len(new_snapshots))

        # 4️⃣  Build liquidity map for RewardCalculator --------------------
        liq_map: Dict[int, Dict[int, float]] = defaultdict(dict)
        for (ck, subnet, _blk), usd_val in aggregated.items():
            uid = self._coldkey_to_uid(ck, subnet)  # implement as needed
            if uid is not None:
                liq_map[subnet][uid] = usd_val

        self.cache.liquidity = liq_map
        return new_snapshots

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _exists(
        self, db: Session, wallet: str, subnet: int, block_height: int
    ) -> bool:
        """Quick existence test to avoid duplicate rows."""
        return (
            db.query(LiquiditySnapshot)
            .filter_by(
                wallet_hotkey=wallet,
                subnet_id=subnet,
                block_height=block_height,
            )
            .first()
            is not None
        )

    # ------------------------------------------------------------------ #
    # Default async fetch implementation
    # ------------------------------------------------------------------ #
    async def _default_fetch(
        self,
        *,
        netuid: Optional[int],
        block: Optional[int],
    ) -> List[LiquiditySubnet]:
        """
        Uses `AsyncSubtensor` + `fetch_subnet_liquidity_positions` to pull
        on‑chain data in an async fashion.
        """
        async with AsyncSubtensor(network=settings.BITTENSOR_NETWORK) as subtensor:
            return await fetch_subnet_liquidity_positions(
                subtensor,
                netuid=netuid,
                block=block,
                max_concurrency=settings.MAX_CONCURRENCY,
                logprogress=False,
            )

    # ------------------------------------------------------------------ #
    # Stub – implement wallet→uid mapping for your subnet
    # ------------------------------------------------------------------ #
    def _coldkey_to_uid(self, coldkey: str, subnet_id: int) -> Optional[int]:
        """
        Map a coldkey string to a miner UID on the given subnet.

        Replace with real logic; returns None when mapping is unknown.
        """
        return None
