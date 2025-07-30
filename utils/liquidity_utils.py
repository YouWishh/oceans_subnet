"""
Async helpers for fetching liquidity positions on Bittensor subnets.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import bittensor as bt                              # ← NEW logging
from bittensor import AsyncSubtensor
from bittensor.utils.liquidity import LiquidityPosition

from utils.subnet_utils import get_metagraph

# -------------------------------------------------------------------- #
# Config & logging
# -------------------------------------------------------------------- #
_SOURCE_NETUID = 66
_INACTIVE_SUBNETS: Set[int] = {
    0,  # ALWAYS exclude 0
    15, 46, 67, 69, 74, 78, 82, 83, 95, 100,
    101, 104, 110, 112, 115, 116, 117, 118, 119, 120,
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)
logger = logging.getLogger(__name__)

# -------------------------------------------------------------------- #
# Data classes
# -------------------------------------------------------------------- #
@dataclass(slots=True)
class LiquiditySubnet:
    netuid: int
    coldkey_positions: Dict[str, List[LiquidityPosition]] = field(repr=False)

    @property
    def unique_coldkeys(self) -> int:  # noqa: D401
        return len(self.coldkey_positions)

    @property
    def total_positions(self) -> int:  # noqa: D401
        return sum(len(v) for v in self.coldkey_positions.values())

    def __repr__(self) -> str:  # noqa: D401
        head = (
            f"<LiquiditySubnet netuid={self.netuid} "
            f"coldkeys={self.unique_coldkeys} "
            f"positions={self.total_positions}>"
        )
        lines = [head]
        for ck, plist in self.coldkey_positions.items():
            lines.append(f"  {ck}: {len(plist)} positions")
            for p in plist:
                lines.append(f"    {p}")
        lines.append("</LiquiditySubnet>")
        return "\n".join(lines)


@dataclass(slots=True)
class _StubPublicKey:
    ss58_address: str


@dataclass(slots=True)
class _StubWallet:
    _cold_ss58: str

    @property
    def coldkeypub(self) -> _StubPublicKey:  # noqa: D401
        return _StubPublicKey(self._cold_ss58)

# -------------------------------------------------------------------- #
# Internal helpers
# -------------------------------------------------------------------- #
async def _discover_subnets(st: AsyncSubtensor) -> List[int]:
    """
    Return a **sorted** list of all active subnet IDs, excluding those
    in `_INACTIVE_SUBNETS`.
    """
    candidates: List[int] = []
    if hasattr(st, "get_subnets"):
        try:
            subnets = await st.get_subnets()  # type: ignore[arg-type]
            candidates = [int(x) for x in subnets]
        except Exception as err:  # noqa: BLE001
            logger.debug("get_subnets() failed: %s", err)

    if not candidates:
        for attr in ("subnet_count", "get_subnet_count"):
            if hasattr(st, attr):
                try:
                    count = await getattr(st, attr)()  # type: ignore[misc]
                    candidates = list(range(int(count)))
                    break
                except Exception as err:  # noqa: BLE001
                    logger.debug("%s() failed: %s", attr, err)

    active = sorted(uid for uid in candidates if uid not in _INACTIVE_SUBNETS)
    bt.logging.info(
        f"[liquidity_utils] Discovered {len(active)} active subnets "
        f"(excluded {_INACTIVE_SUBNETS})"
    )
    return active

# -------------------------------------------------------------------- #
# Public API
# -------------------------------------------------------------------- #
async def fetch_subnet_liquidity_positions(
    subtensor: AsyncSubtensor,
    netuid: Optional[int] = None,
    *,
    block: Optional[int] = None,
    max_concurrency: int = 20,
    logprogress: bool = True,
) -> List[LiquiditySubnet]:
    """
    Retrieve liquidity positions.  Excludes subnet 0 and any IDs in
    `_INACTIVE_SUBNETS`.
    """
    # 0️⃣  Load coldkeys once from the SOURCE subnet ----------------------
    metagraph_src = await get_metagraph(
        _SOURCE_NETUID, st=subtensor, lite=True, block=block
    )
    src_coldkeys: List[str] = list(dict.fromkeys(metagraph_src.coldkeys or []))
    if not src_coldkeys:
        raise RuntimeError(
            f"Metagraph of subnet {_SOURCE_NETUID} returned no coldkeys."
        )

    # 1️⃣  Decide which subnets to query ---------------------------------
    if netuid is None:
        targets = await _discover_subnets(subtensor)
    else:
        if netuid in _INACTIVE_SUBNETS:
            bt.logging.warning(f"[liquidity_utils] Subnet {netuid} is inactive – skipping")
            return []
        targets = [netuid]

    # 2️⃣  Convenience logger -------------------------------------------
    bt.logging.info(f"[liquidity_utils] Querying subnets: {targets}")

    # Helper: query a single subnet
    async def _query_single_subnet(uid: int) -> LiquiditySubnet:
        if logprogress:
            print(f"\n=== Fetching subnet {uid} ===", flush=True)

        semaphore = asyncio.Semaphore(max_concurrency)

        async def _query_single_ck(cold_ss58: str) -> Tuple[str, List[LiquidityPosition]]:
            async with semaphore:
                wallet_stub = _StubWallet(cold_ss58)
                try:
                    positions = await subtensor.get_liquidity_list(
                        wallet=wallet_stub,
                        netuid=uid,
                        block=block,
                        reuse_block=block is None,
                    )
                    positions = positions or []
                    bt.logging.debug(
                        f"[liquidity_utils] subnet {uid} coldkey {cold_ss58[:6]}… "
                        f"→ {len(positions)} positions"
                    )
                    return cold_ss58, positions
                except Exception as err:  # noqa: BLE001
                    logger.warning("[%s] error fetching positions: %s", cold_ss58[:6], err)
                    return cold_ss58, []

        tasks = [_query_single_ck(ck) for ck in src_coldkeys]
        results = await asyncio.gather(*tasks)
        return LiquiditySubnet(uid, {ck: pos for ck, pos in results})

    # 3️⃣  Fan‑out over all targets --------------------------------------
    liquidity_subnets = []
    for uid in targets:
        ls = await _query_single_subnet(uid)
        liquidity_subnets.append(ls)

    bt.logging.info(
        f"[liquidity_utils] Finished – collected liquidity for {len(liquidity_subnets)} subnets"
    )
    return liquidity_subnets
