"""
Validator‑side helper that pulls liquidity data from chain, converts it
into storage‑layer snapshots and persists only *new* records.

All amounts are denominated in **TAO**.
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
from utils.subnet_utils import get_metagraph     # metagraph helper

log = logging.getLogger("validator.liquidity_fetcher")


class LiquidityFetcher:
    """
    Fetches on‑chain liquidity, stores snapshots, and updates
    `cache.liquidity` in the shape that `RewardCalculator` expects:

        cache.liquidity = { subnet_id: { uid: tao_value, … }, … }
    """

    def __init__(
        self,
        cache: StateCache,
        *,
        fetch_fn: Optional[
            Callable[..., Awaitable[List[LiquiditySubnet]]]
        ] = None,   # injectable in unit‑tests
    ) -> None:
        self.cache = cache
        self._fetch_fn = fetch_fn or self._default_fetch

        # LR‑style cache: (coldkey, subnet) → uid
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
        if netuid == 0:
            bt.logging.warning("[LiquidityFetcher] Ignoring request for subnet 0")
            return []

        bt.logging.info(f"[LiquidityFetcher] Fetching liquidity (netuid={netuid})…")

        # 1️⃣  Download liquidity ----------------------------------------
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

        # 2️⃣  Make sure UID cache covers every (ck, subnet) pair ---------
        await self._populate_uid_cache(liquidity_subnets, block=block)

        # 3️⃣  Aggregate TAO by coldkey & subnet -------------------------
        aggregated: Dict[Tuple[str, int, int], float] = {}

        def _balance_to_tao(bal: Balance) -> float:
            # Safe conversion no matter how Balance is implemented
            try:
                return float(bal)            # Balance.__float__ → tao
            except Exception:
                if hasattr(bal, "tao"):
                    return float(bal.tao)
                if hasattr(bal, "rao"):
                    return float(bal.rao) / 1e9
                raise

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
                aggregated[(coldkey, ls.netuid, block or 0)] = tao_total

        # 4️⃣  Persist LiquiditySnapshot rows (only tao_total > 0) --------
        new_snapshots: List[LiquiditySnapshot] = []
        with self.cache._session() as db:  # pylint: disable=protected-access
            for (ck, subnet, blk), tao_val in aggregated.items():
                if tao_val <= 0.0:
                    continue
                if not self._exists(db, ck, subnet, blk):
                    bt.logging.debug(
                        f"[LiquidityFetcher] New snapshot: {ck[:6]}… "
                        f"subnet {subnet} blk {blk} → {tao_val:.9f} TAO"
                    )
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
        bt.logging.debug(f"Creating liquidity map")
        for (ck, subnet, _blk), tao_val in aggregated.items():
            if tao_val <= 0.0:
                continue  # ignore empty entries
            bt.logging.debug(f"Liquidity map -> tao_val: {tao_val:.9f} TAO for {ck[:6]}… on subnet {subnet}")
            uid = self._ck_uid_cache.get((ck, subnet))
            if uid is None:
                continue  # coldkey not present on that subnet
            bt.logging.debug(f"Liquidity map -> tao_val: {tao_val:.9f} TAO for {ck[:6]}… on subnet {subnet}")    
            liq_map[subnet][uid] = tao_val
            bt.logging.debug(
                f"[LiquidityFetcher] Liquidity map entry: subnet {subnet} "
                f"uid {uid} → {tao_val:.9f} TAO"
            )

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
    # UID‑cache handling ------------------------------------------------ #
    # ------------------------------------------------------------------ #
    async def _populate_uid_cache(
        self,
        liquidity_subnets: List[LiquiditySubnet],
        *,
        block: Optional[int],
    ) -> None:
        """
        Make sure `_ck_uid_cache` includes every coldkey that appeared in
        the freshly fetched liquidity results (keyed per subnet).
        """
        needed: Dict[int, set[str]] = defaultdict(set)

        # Collect any (ck, subnet) not in cache yet
        for ls in liquidity_subnets:
            for ck in ls.coldkey_positions:
                key = (ck, ls.netuid)
                if key not in self._ck_uid_cache:
                    needed[ls.netuid].add(ck)

        if not needed:
            return  # cache already complete

        bt.logging.info(
            f"[LiquidityFetcher] Loading UID maps for subnets: {sorted(needed)}"
        )

        async with AsyncSubtensor(network=settings.BITTENSOR_NETWORK) as subtensor:
            for subnet_id, coldkeys in needed.items():
                try:
                    mg = await get_metagraph(
                        subnet_id, st=subtensor, lite=True, block=block
                    )
                    for uid, ck in zip(mg.uids, mg.coldkeys):
                        if ck in coldkeys:
                            self._ck_uid_cache[(str(ck), subnet_id)] = int(uid)
                    bt.logging.debug(
                        f"[LiquidityFetcher]   subnet {subnet_id}: "
                        f"cached {len(coldkeys)} coldkeys"
                    )
                except Exception as e:  # noqa: BLE001
                    bt.logging.warning(
                        f"[LiquidityFetcher] Could not fetch metagraph {subnet_id}: {e}"
                    )
                    for ck in coldkeys:
                        self._ck_uid_cache[(ck, subnet_id)] = None
