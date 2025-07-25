"""
Validator‑side helper that pulls liquidity data from the chain, converts it
into storage‑layer snapshots and persists only *new* records.

Usage
-----
>>> cache   = StateCache()
>>> fetcher = LiquidityFetcher(cache)
>>> new_liq = fetcher.fetch_and_store()          # run once per epoch
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Dict, List, Optional, Tuple

from bittensor import AsyncSubtensor  # type: ignore
from sqlalchemy.orm import Session

from config import settings
from storage.models import LiquiditySnapshot
from validator.state_cache import StateCache
from collections import defaultdict
from utils.liquidity_utils import (
    LiquiditySubnet,
    fetch_subnet_liquidity_positions,
)

log = logging.getLogger("validator.liquidity_fetcher")


class LiquidityFetcher:
    """
    Wraps the *async* helpers in `utils.liquidity_utils` with a synchronous,
    validator‑friendly façade and transparent de‑duplication.
    """

    # --------------------------------------------------------------------- #
    # Construction
    # --------------------------------------------------------------------- #
    def __init__(
        self,
        cache: StateCache,
        *,
        fetch_fn: Optional[
            Callable[..., List[LiquiditySubnet]]
        ] = None,  # injected for unit‑tests
    ) -> None:
        self.cache = cache
        self._fetch_fn = fetch_fn or self._default_fetch

    # --------------------------------------------------------------------- #
    # Public entry‑point
    # --------------------------------------------------------------------- #
    def fetch_and_store(
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
        liquidity_subnets: List[LiquiditySubnet] = self._fetch_fn(
            netuid=netuid,
            block=block,
        )

        # 1️⃣  Flatten & aggregate USD value per wallet/subnet  -----------------
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

        # 2️⃣  Convert to LiquiditySnapshot objects (dedup via DB)  -------------
        new_snapshots: List[LiquiditySnapshot] = []
        with self.cache._session() as db:  # pylint: disable=protected-access
            for (ck, subnet, blk), usd_val in aggregated.items():
                if usd_val == 0.0:  # skip empty wallets
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
        
        liq_map: Dict[int, Dict[int, float]] = defaultdict(dict)
        for (ck, subnet, blk), usd_val in aggregated.items():
            # Assuming you have a reliable coldkey→uid lookup:
            uid = self._coldkey_to_uid(ck, subnet)
            if uid is not None:
                liq_map[subnet][uid] = usd_val

        self.cache.liquidity = liq_map

        return new_snapshots

    # --------------------------------------------------------------------- #
    # Helpers
    # --------------------------------------------------------------------- #
    def _exists(
        self, db: Session, wallet: str, subnet: int, block_height: int
    ) -> bool:
        """
        Quick existence test to avoid duplicate rows.
        """
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

    # --------------------------------------------------------------------- #
    # Default async fetch implementation
    # --------------------------------------------------------------------- #
    def _default_fetch(
        self,
        *,
        netuid: Optional[int],
        block: Optional[int],
    ) -> List[LiquiditySubnet]:
        """
        Bridges the async helper into synchronous code by running its
        coroutine inside a private event‑loop.
        """

        async def _inner() -> List[LiquiditySubnet]:
            async with AsyncSubtensor(
                network=settings.BITTENSOR_NETWORK
            ) as subtensor:
                return await fetch_subnet_liquidity_positions(
                    subtensor,
                    netuid=netuid,
                    block=block,
                    max_concurrency=settings.MAX_CONCURRENCY,
                    logprogress=False,
                )

        return asyncio.run(_inner())
