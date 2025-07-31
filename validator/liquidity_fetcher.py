"""
Validator‑side helper that pulls liquidity data from chain, converts it
into storage‑layer snapshots and persists only *new* records.

`fetch_and_store()` is **async** and can be awaited inside the validator’s
event loop without spawning a nested loop.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

import bittensor as bt
from bittensor import AsyncSubtensor            # type: ignore
from bittensor.utils.balance import Balance
from sqlalchemy.orm import Session

from config import settings
from storage.models import LiquiditySnapshot
from validator.state_cache import StateCache
from utils.liquidity_utils import (
    LiquiditySubnet,
    fetch_subnet_liquidity_positions,
)
from utils.subnet_utils import get_metagraph     # ← used for the UID lookup

log = logging.getLogger("validator.liquidity_fetcher")


class LiquidityFetcher:
    """
    Wraps the *async* helpers in `utils.liquidity_utils` with an async,
    validator‑friendly façade and transparent de‑duplication.

    All amounts are denominated in **TAO** (not USD).
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
        ] = None,
    ) -> None:
        self.cache = cache
        self._fetch_fn = fetch_fn or self._default_fetch

        # Local LRU‑ish cache: (coldkey, subnet) → uid
        self._ck_uid_cache: Dict[Tuple[str, int], Optional[int]] = {}

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
        • Collect liquidity from chain  
        • Aggregate TAO value per (wallet, subnet)  
        • Store only new (wallet, subnet, block) combinations  
        • Return the list of *persisted* snapshots
        """
        if netuid == 0:
            bt.logging.warning("[LiquidityFetcher] Ignoring request for subnet 0")
            return []

        bt.logging.info(f"[LiquidityFetcher] Fetching liquidity (netuid={netuid})…")

        # 1️⃣  Fetch liquidity objects ------------------------------------
        if asyncio.iscoroutinefunction(self._fetch_fn):
            liquidity_subnets = await self._fetch_fn(netuid=netuid, block=block)
        else:
            liquidity_subnets = await asyncio.to_thread(
                self._fetch_fn, netuid=netuid, block=block
            )

        bt.logging.info(
            f"[LiquidityFetcher] Retrieved {len(liquidity_subnets)} "
            f"LiquiditySubnet objects"
        )

        # 2️⃣  Ensure we have coldkey→uid maps for every subnet -----------
        await self._populate_uid_cache(liquidity_subnets, block=block)

        # 3️⃣  Flatten & aggregate TAO value per wallet/subnet ------------
        aggregated: Dict[Tuple[str, int, int], float] = {}

        def _balance_to_tao(bal: Balance) -> float:
            if hasattr(bal, "tao"):
                return float(bal.tao)
            if hasattr(bal, "rao"):
                return float(bal.rao) / 1e9
            return float(bal)

        for ls in liquidity_subnets:
            bt.logging.info(
                f"[LiquidityFetcher] Subnet {ls.netuid} → "
                f"{ls.unique_coldkeys} coldkeys, {ls.total_positions} positions"
            )
            for coldkey, positions in ls.coldkey_positions.items():
                for pos in positions:
                    bt.logging.debug(
                        f"[LiquidityFetcher]     {coldkey[:6]}… position: {pos}"
                    )

                tao_total = sum(_balance_to_tao(p.liquidity) for p in positions)
                key = (coldkey, ls.netuid, block or 0)
                aggregated[key] = tao_total

        # 4️⃣  Persist new LiquiditySnapshot rows -------------------------
        new_snapshots: List[LiquiditySnapshot] = []
        with self.cache._session() as db:  # pylint: disable=protected-access
            for (ck, subnet, blk), tao_val in aggregated.items():
                if tao_val == 0.0:
                    continue
                if not self._exists(db, ck, subnet, blk):
                    new_snapshots.append(
                        LiquiditySnapshot(
                            wallet_hotkey=ck,
                            subnet_id=subnet,
                            tao_value=tao_val,
                            block_height=blk,
                        )
                    )

        if new_snapshots:
            self.cache.persist_liquidity(new_snapshots)
            bt.logging.info(
                f"[LiquidityFetcher] Persisted {len(new_snapshots)} new snapshots"
            )
        else:
            bt.logging.debug("[LiquidityFetcher] No new liquidity snapshots to store")

        # 5️⃣  Build liquidity map for RewardCalculator -------------------
        liq_map: Dict[int, Dict[int, float]] = defaultdict(dict)
        for (ck, subnet, _blk), tao_val in aggregated.items():
            uid = self._coldkey_to_uid(ck, subnet)
            if uid is not None:
                liq_map[subnet][uid] = tao_val

        self.cache.liquidity = liq_map
        bt.logging.info(
            f"[LiquidityFetcher] liquidity map updated "
            f"({len(liq_map)} subnets, total {sum(len(v) for v in liq_map.values())} UIDs)"
        )
        return new_snapshots

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _exists(db: Session, wallet: str, subnet: int, block_height: int) -> bool:
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

    async def _default_fetch(
        self,
        *,
        netuid: Optional[int],
        block: Optional[int],
    ) -> List[LiquiditySubnet]:
        async with AsyncSubtensor(network=settings.BITTENSOR_NETWORK) as subtensor:
            return await fetch_subnet_liquidity_positions(
                subtensor,
                netuid=netuid,
                block=block,
                max_concurrency=settings.MAX_CONCURRENCY,
                logprogress=False,
            )

    # ------------------------------------------------------------------ #
    # Coldkey→UID mapping ------------------------------------------------ #
    # ------------------------------------------------------------------ #
    async def _populate_uid_cache(
        self,
        liquidity_subnets: List[LiquiditySubnet],
        *,
        block: Optional[int],
    ) -> None:
        """
        Ensure `_ck_uid_cache` contains all (coldkey, subnet) pairs present
        in the freshly fetched liquidity data.
        """
        needed: Dict[int, set[str]] = defaultdict(set)
        for ls in liquidity_subnets:
            for ck in ls.coldkey_positions:
                if (ck, ls.netuid) not in self._ck_uid_cache:
                    needed[ls.netuid].add(ck)

        if not needed:
            return  # everything already cached

        async with AsyncSubtensor(network=settings.BITTENSOR_NETWORK) as subtensor:
            for subnet_id, _missing in needed.items():
                try:
                    mg = await get_metagraph(
                        subnet_id, st=subtensor, lite=True, block=block
                    )
                    # The metagraph’s coldkeys are in UID order
                    for uid, ck in zip(mg.uids, mg.coldkeys):
                        self._ck_uid_cache[(str(ck), subnet_id)] = int(uid)
                except Exception as e:  # noqa: BLE001
                    bt.logging.warning(
                        f"[LiquidityFetcher] Could not fetch metagraph {subnet_id}: {e}"
                    )

    def _coldkey_to_uid(self, coldkey: str, subnet_id: int) -> Optional[int]:
        """
        Fast lookup from the local cache.  None is returned when the UID
        is unknown – callers must handle that.
        """
        return self._ck_uid_cache.get((coldkey, subnet_id))
