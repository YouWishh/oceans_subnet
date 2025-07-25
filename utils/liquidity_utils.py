# utils/liquidity_utils.py
"""
Async helpers for fetching liquidity positions on Bittensor subnets.

What’s new (2025‑07‑16)
-----------------------
* Added global constant ``_SOURCE_NETUID = 66``.
* Coldkeys are loaded **once** from that subnet and reused for
  every liquidity‑position query (SN 1 → SN N).
* No other public API changed – signatures and return types are intact.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ────────────────────────────────────────────────────────────────────────────
# Config & logging
# ────────────────────────────────────────────────────────────────────────────
_SOURCE_NETUID = 66        # ← change here if you ever need a new source subnet

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)
logger = logging.getLogger(__name__)

from bittensor import AsyncSubtensor  
from bittensor.utils.liquidity import LiquidityPosition  

from utils.subnet_utils import get_metagraph  

# ────────────────────────────────────────────────────────────────────────────
# Data objects
# ────────────────────────────────────────────────────────────────────────────
@dataclass(slots=True)
class LiquiditySubnet:
    """
    Container holding **all** liquidity positions for a single subnet.

    Attributes
    ----------
    netuid
        Subnet identifier.
    coldkey_positions
        Mapping: coldkey → list of ``LiquidityPosition`` objects.
    """
    netuid: int
    coldkey_positions: Dict[str, List[LiquidityPosition]] = field(repr=False)

    # Quality‑of‑life helpers
    @property
    def unique_coldkeys(self) -> int:        # noqa: D401
        return len(self.coldkey_positions)

    @property
    def total_positions(self) -> int:        # noqa: D401
        return sum(len(v) for v in self.coldkey_positions.values())

    # Readable __repr__ for prints / logs
    def __repr__(self) -> str:               # noqa: D401
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

# ────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ────────────────────────────────────────────────────────────────────────────
@dataclass(slots=True)
class _StubPublicKey:
    ss58_address: str


@dataclass(slots=True)
class _StubWallet:
    _cold_ss58: str

    @property
    def coldkeypub(self) -> _StubPublicKey:  # noqa: D401
        return _StubPublicKey(self._cold_ss58)


async def _discover_subnets(st: AsyncSubtensor) -> List[int]:
    """
    Return a **sorted list** of all known subnet IDs.
    (Unchanged from previous release.)
    """
    if hasattr(st, "get_subnets"):
        try:
            subnets = await st.get_subnets()  # type: ignore[arg-type]
            return sorted(int(x) for x in subnets)
        except Exception as err:  # noqa: BLE001
            logger.debug("get_subnets() failed: %s", err)

    for attr in ("subnet_count", "get_subnet_count"):
        if hasattr(st, attr):
            try:
                count = await getattr(st, attr)()  # type: ignore[misc]
                return list(range(int(count)))
            except Exception as err:  # noqa: BLE001
                logger.debug("%s() failed: %s", attr, err)

    logger.warning("Falling back to blind scan (0‑255).")
    discovered: List[int] = []
    for uid in range(256):
        try:
            await get_metagraph(uid, st=st, lite=True)
            discovered.append(uid)
        except Exception:
            break
    return discovered


# ────────────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────────────
async def fetch_subnet_liquidity_positions(
    subtensor: AsyncSubtensor,
    netuid: Optional[int] = None,
    *,
    block: Optional[int] = None,
    max_concurrency: int = 20,
    logprogress: bool = True,
) -> List[LiquiditySubnet]:
    """
    Retrieve liquidity positions.

    Parameters
    ----------
    netuid
        • When **int** → query *that subnet only*.<br>
        • When **None** → query *all* subnets (discovered automatically).
    block
        Historical Polkadot/Kusama block. ``None`` = chain head.
    max_concurrency
        Semaphore size to limit simultaneous RPC calls.
    logprogress
        Emit human‑friendly progress prints.

    Returns
    -------
    List[LiquiditySubnet]
        One element per subnet, ordered by increasing ``netuid``.
    """
    # 0️⃣  Load coldkeys once from the SOURCE subnet ------------------------------
    if logprogress:
        print(f"→ Loading coldkeys from subnet {_SOURCE_NETUID} …", flush=True)

    metagraph_src = await get_metagraph(
        _SOURCE_NETUID, st=subtensor, lite=True, block=block
    )
    all_src_coldkeys: List[str] = metagraph_src.coldkeys or []
    src_coldkeys: List[str] = list(dict.fromkeys(all_src_coldkeys))

    if not src_coldkeys:
        raise RuntimeError(
            f"Metagraph of subnet {_SOURCE_NETUID} returned no coldkeys."
        )

    if logprogress:
        print(
            f"✓ Source metagraph fetched – {len(all_src_coldkeys)} miners, "
            f"{len(src_coldkeys)} unique coldkeys.",
            flush=True,
        )

    # Decide which subnets to touch ------------------------------------------------
    targets: List[int]
    if netuid is None:
        if logprogress:
            print("→ Discovering subnets …", flush=True)
        targets = await _discover_subnets(subtensor)
        if logprogress:
            print(
                f"✓ Found {len(targets)} subnets "
                f"[{', '.join(map(str, targets))}].",
                flush=True,
            )
    else:
        targets = [netuid]

    # Helper: query a *single* subnet ---------------------------------------------
    async def _query_single_subnet(uid: int) -> LiquiditySubnet:
        if logprogress:
            print(f"\n=== Fetching subnet {uid} ===", flush=True)

        # 1️⃣  Use the *already‑fetched* coldkeys from subnet 66
        unique_coldkeys: List[str] = src_coldkeys
        if logprogress:
            print(
                f"✓ Using {len(unique_coldkeys)} coldkeys "
                f"from subnet {_SOURCE_NETUID}.",
                flush=True,
            )

        # 2️⃣  Query every coldkey in parallel (same implementation as before)
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
                    if logprogress:
                        head = f"  • [{cold_ss58[:6]}] {len(positions)} positions"
                        print(head, flush=True)
                        if positions:
                            for p in positions:
                                print(f"      {p}", flush=True)
                    return cold_ss58, positions

                except Exception as err:  # noqa: BLE001
                    logger.warning("[%s] error fetching positions: %s", cold_ss58[:6], err)
                    if logprogress:
                        print(f"  • [{cold_ss58[:6]}] ERROR – see logs", flush=True)
                    return cold_ss58, []

        if logprogress:
            print(
                f"→ Querying {len(unique_coldkeys)} coldkeys "
                f"(max_concurrency={max_concurrency}) …",
                flush=True,
            )

        tasks = [_query_single_ck(ck) for ck in unique_coldkeys]
        results = await asyncio.gather(*tasks)
        if logprogress:
            print("✓ All queries complete.", flush=True)

        return LiquiditySubnet(uid, {ck: pos for ck, pos in results})

    # ── Fan‑out over all targeted subnets ────────────────────────────────────────
    liquidity_subnets: List[LiquiditySubnet] = []
    for idx, uid in enumerate(targets, 1):
        if logprogress and netuid is None:
            print(f"\n[{idx}/{len(targets)}] -------------------------------------------------")
        ls = await _query_single_subnet(uid)
        liquidity_subnets.append(ls)

    return liquidity_subnets


# ────────────────────────────────────────────────────────────────────────────
# CLI (“python -m utils.liquidity_utils”)
# ────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Inspect liquidity positions.\n"
            "• With --subnet N  : query that single subnet.\n"
            "• Without --subnet : query ALL subnets."
        )
    )
    parser.add_argument(
        "--subnet",
        "--netuid",
        type=int,
        default=None,
        help="Subnet ID to inspect. Omit to scan all.",
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=20,
        help="Limit simultaneous RPC calls (default: 20).",
    )
    parser.add_argument(
        "--block",
        type=int,
        default=None,
        help="Historical block number (omit for chain head).",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"],
        help="Root logger level (default: INFO).",
    )
    args = parser.parse_args()
    logging.getLogger().setLevel(getattr(logging, args.log_level.upper(), logging.INFO))

    # Pretty‑print summary for one subnet ----------------------------------------
    def _print_subnet_summary(ls: LiquiditySubnet) -> None:
        print(
            f"\n=== SUBNET {ls.netuid} SUMMARY ===\n"
            f"Unique coldkeys   : {ls.unique_coldkeys}\n"
            f"Total positions   : {ls.total_positions}\n",
            flush=True,
        )
        for ck, plist in ls.coldkey_positions.items():
            print(f"{ck}: {len(plist)} positions")
            for p in plist:
                print(f"    {p}")
        print(flush=True)

    # Entrypoint -----------------------------------------------------------------
    async def _main() -> None:
        async with AsyncSubtensor() as st:
            all_liquidity = await fetch_subnet_liquidity_positions(
                st,
                netuid=args.subnet,               # may be None
                block=args.block,
                max_concurrency=args.max_concurrency,
                logprogress=True,
            )

            # If user asked for one subnet -> show detailed human summary
            if args.subnet is not None and all_liquidity:
                _print_subnet_summary(all_liquidity[0])

            # ↓↓↓ print the *entire* object
            print("\n=== FINAL LIQUIDITY OBJECT ===")
            for ls in all_liquidity:
                print(ls)      # uses LiquiditySubnet.__repr__()
                print()        # blank line between subnets

    asyncio.run(_main())
