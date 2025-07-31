"""
Async helpers for fetching liquidity positions on Bittensor subnets.

Only subnets listed in ``api.client.ACTIVE_SUBNETS`` are queried.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import bittensor as bt
from bittensor import AsyncSubtensor
from bittensor.utils.liquidity import LiquidityPosition

# -------------------------------------------------------------------- #
# Imports from local modules
# -------------------------------------------------------------------- #
from api.client import ACTIVE_SUBNETS                 # ← NEW
from utils.subnet_utils import get_metagraph

# -------------------------------------------------------------------- #
# Config & logging
# -------------------------------------------------------------------- #
_SOURCE_NETUID = 66  # used only to discover coldkeys
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
    Retrieve liquidity positions for subnets in ``api.client.ACTIVE_SUBNETS``.

    Parameters
    ----------
    subtensor:
        An initialised :class:`bittensor.AsyncSubtensor` instance.
    netuid:
        If provided, query **only** this subnet.  The subnet must be in
        ``ACTIVE_SUBNETS``; otherwise the call returns an empty list and logs
        a warning.
    block:
        Specific block height to query.  ``None`` means *latest*.
    max_concurrency:
        Maximum number of concurrent coldkey queries per subnet.
    logprogress:
        If *True*, prints a progress message before each subnet is fetched.
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
        targets: List[int] = sorted(ACTIVE_SUBNETS)
    else:
        if netuid not in ACTIVE_SUBNETS:
            bt.logging.warning(
                f"[liquidity_utils] Subnet {netuid} is not in ACTIVE_SUBNETS – skipping"
            )
            return []
        targets = [netuid]

    bt.logging.info(f"[liquidity_utils] Querying subnets: {targets}")

    # Helper: query a single subnet
    async def _query_single_subnet(uid: int) -> LiquiditySubnet:
        if logprogress:
            print(f"\n=== Fetching subnet {uid} ===", flush=True)

        semaphore = asyncio.Semaphore(max_concurrency)

        async def _query_single_ck(
            cold_ss58: str,
        ) -> Tuple[str, List[LiquidityPosition]]:
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

                    # Always print the headline line …
                    bt.logging.debug(
                        f"[liquidity_utils] subnet {uid} coldkey {cold_ss58[:6]}… "
                        f"→ {len(positions)} positions"
                    )

                    # … and, **only when positions exist**, print each one.
                    if positions:
                        for p in positions:
                            bt.logging.debug(
                                f"[liquidity_utils] subnet {uid} coldkey {cold_ss58[:6]}…     {p}"
                            )

                    return cold_ss58, positions
                except Exception as err:  # noqa: BLE001
                    logger.warning(
                        "[%s] error fetching positions: %s", cold_ss58[:6], err
                    )
                    return cold_ss58, []

        tasks = [_query_single_ck(ck) for ck in src_coldkeys]
        results = await asyncio.gather(*tasks)
        return LiquiditySubnet(uid, {ck: pos for ck, pos in results})

    # 2️⃣  Fan‑out over all targets --------------------------------------
    liquidity_subnets = []
    for uid in targets:
        ls = await _query_single_subnet(uid)
        liquidity_subnets.append(ls)

    bt.logging.info(
        f"[liquidity_utils] Finished – collected liquidity for "
        f"{len(liquidity_subnets)} subnet(s)"
    )
    return liquidity_subnets
