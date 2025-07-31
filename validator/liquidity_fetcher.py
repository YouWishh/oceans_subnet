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
from utils.subnet_utils import get_metagraph

log = logging.getLogger("validator.liquidity_fetcher")


class LiquidityFetcher:
    """
    Fetches on‑chain liquidity, stores snapshots, and updates
    `cache.liquidity` in the form:

        cache.liquidity = { liquidity_subnet_id: { uid_on_66: tao, … }, … }
    """

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    def __init__(
        self,
        cache: StateCache,
        *,
        primary_netuid: int,   # ← validator’s own subnet (66)
        fetch_fn: Optional[
            Callable[..., Awaitable[List[LiquiditySubnet]]]
        ] = None,
    ) -> None:
        self.cache = cache
        self.primary_netuid = int(primary_netuid)
        self._fetch_fn = fetch_fn or self._default_fetch

        # Cache: coldkey → UID (on subnet 66)
        self._primary_uid_map: Dict[str, int] = {}
        self._primary_loaded: bool = False

    # ------------------------------------------------------------------ #
    # PUBLIC – async entry‑point                                         #
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

        # 2️⃣  Ensure primary‑subnet UID map is loaded once --------------
        if not self._primary_loaded:
            await self._load_primary_uid_map(block=block)

        # 3️⃣  Aggregate TAO per coldkey / subnet ------------------------
        aggregated: Dict[Tuple[str, int, int], float] = {}

        def _bal_to_tao(b: Balance) -> float:
            try:
                return float(b)  # Balance.__float__
            except Exception:
                return float(getattr(b, "tao", getattr(b, "rao", 0) / 1e9))

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
                aggregated[(coldkey, ls.netuid, block or 0)] = sum(
                    _bal_to_tao(p.liquidity) for p in positions
                )

        # 4️⃣  Persist new LiquiditySnapshot rows ------------------------
        new_rows: List[LiquiditySnapshot] = []
        with self.cache._session() as db:  # pylint: disable=protected-access
            for (ck, subnet, blk), tao_val in aggregated.items():
                if tao_val <= 0.0:
                    continue
                if not self._exists(db, ck, subnet, blk):
                    bt.logging.debug(
                        f"[LiquidityFetcher] New snapshot: {ck[:6]}… "
                        f"subnet {subnet} blk {blk} → {tao_val:.9f} TAO"
                    )
                    new_rows.append(
                        LiquiditySnapshot(
                            wallet_hotkey=ck,
                            subnet_id=subnet,
                            tao_value=tao_val,
                            block_height=blk,
                        )
                    )

        if new_rows:
            self.cache.persist_liquidity(new_rows)
            bt.logging.info(f"[LiquidityFetcher] Persisted {len(new_rows)} snapshots")

        # 5️⃣  Build liquidity map for RewardCalculator ------------------
        liq_map: Dict[int, Dict[int, float]] = defaultdict(dict)

        for (ck, subnet, _), tao_val in aggregated.items():
            if tao_val <= 0:
                continue
            uid = self._primary_uid_map.get(ck)
            if uid is None:
                bt.logging.debug(
                    f"[LiquidityFetcher] Coldkey {ck[:6]}… not on subnet "
                    f"{self.primary_netuid} – skipped"
                )
                continue
            liq_map[subnet][uid] = tao_val
            bt.logging.debug(
                f"[LiquidityFetcher] Map entry: subnet {subnet} uid {uid} "
                f"→ {tao_val:.9f} TAO"
            )

        self.cache.liquidity = liq_map
        bt.logging.info(
            f"[LiquidityFetcher] liquidity map updated "
            f"({len(liq_map)} subnets, total "
            f"{sum(len(v) for v in liq_map.values())} UIDs)"
        )
        return new_rows

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

    # ---------- UID map (coldkey → UID on subnet 66) ------------------- #
    async def _load_primary_uid_map(self, *, block: Optional[int]) -> None:
        bt.logging.info(
            f"[LiquidityFetcher] Loading UID map for subnet {self.primary_netuid}"
        )
        async with AsyncSubtensor(network=settings.BITTENSOR_NETWORK) as subtensor:
            mg = await get_metagraph(
                self.primary_netuid, st=subtensor, lite=True, block=block
            )
            self._primary_uid_map = {
                str(ck): int(uid) for uid, ck in zip(mg.uids, mg.coldkeys)
            }
        self._primary_loaded = True
        bt.logging.info(
            f"[LiquidityFetcher] UID map loaded ({len(self._primary_uid_map)} entries)"
        )
