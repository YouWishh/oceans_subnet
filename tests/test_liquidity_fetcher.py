"""
Unit‑tests for ``validator.liquidity_fetcher.LiquidityFetcher``.

The suite

* spins up an **in‑memory SQLite** database (isolated per test);
* injects a **stub fetch‑function** that returns deterministic liquidity
  for three subnets (1, 2, 3);
* patches ``LiquidityFetcher._coldkey_to_uid`` so the validator’s
  coldkey→uid lookup is predictable;
* verifies that
    • snapshots are persisted only once;
    • duplicate calls are de‑duplicated;
    • the `StateCache.liquidity` mapping is populated correctly.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Generator, List, Tuple

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# --------------------------------------------------------------------------- #
# Project imports
# --------------------------------------------------------------------------- #
from validator import state_cache as sc
from validator.liquidity_fetcher import LiquidityFetcher
from storage import models as sm

# --------------------------------------------------------------------------- #
# Pytest fixtures – in‑memory DB + StateCache
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="function")
def cache(monkeypatch) -> Generator[sc.StateCache, None, None]:
    """Fresh StateCache backed by an in‑memory SQLite for every test."""
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    TestSessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    sm.Base.metadata.create_all(engine)

    # patch SessionLocal everywhere it’s imported / used
    monkeypatch.setattr(sm, "SessionLocal", TestSessionLocal, raising=True)
    monkeypatch.setattr(sc, "SessionLocal", TestSessionLocal, raising=True)
    monkeypatch.setattr(sc, "init_db", lambda: None, raising=True)

    _cache = sc.StateCache()
    yield _cache

    engine.dispose()


# --------------------------------------------------------------------------- #
# Lightweight stub objects (no bittensor dependency)
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class _Pos:  # mimics bittensor.utils.liquidity.LiquidityPosition
    usd_value: float


@dataclass(slots=True)
class _LiquiditySubnet:  # mirrors utils.liquidity_utils.LiquiditySubnet
    netuid: int
    coldkey_positions: Dict[str, List[_Pos]] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Deterministic stub fetch‑function
# --------------------------------------------------------------------------- #
def _dummy_fetch_fn(*, netuid=None, block=None) -> List[_LiquiditySubnet]:
    """
    Return liquidity for three subnets (1, 2, 3) or the requested single
    subnet.  Two coldkeys per subnet, deterministic USD values.
    """
    targets = [netuid] if netuid is not None else [1, 2, 3]
    out: List[_LiquiditySubnet] = []
    for uid in targets:
        out.append(
            _LiquiditySubnet(
                netuid=uid,
                coldkey_positions={
                    f"ck{uid}a": [_Pos(usd_value=100 * uid)],
                    f"ck{uid}b": [_Pos(usd_value=200 * uid)],
                },
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Test helpers
# --------------------------------------------------------------------------- #
def _expected_aggregated() -> Dict[Tuple[str, int, int], float]:
    """Ground‑truth mapping shared by assertions below (block‑height=7)."""
    agg: Dict[Tuple[str, int, int], float] = {}
    for uid in (1, 2, 3):
        agg[(f"ck{uid}a", uid, 7)] = 100 * uid
        agg[(f"ck{uid}b", uid, 7)] = 200 * uid
    return agg


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_fetcher_persists_and_deduplicates(cache: sc.StateCache, monkeypatch) -> None:
    """
    • First call → six new snapshots (2 coldkeys × 3 subnets).  
    • Second identical call → 0 new snapshots (deduplicated).  
    • Stored DB rows remain at 6.  
    • `cache.liquidity` dict carries the correct USD values per subnet/uid.
    """
    # deterministic uid mapping (hash‑based but stable for test run)
    monkeypatch.setattr(
        LiquidityFetcher,
        "_coldkey_to_uid",
        lambda self, ck, subnet: abs(hash((ck, subnet))) % 10_000,
        raising=False,
    )

    fetcher = LiquidityFetcher(cache, fetch_fn=_dummy_fetch_fn)

    # 1️⃣  first run
    first = fetcher.fetch_and_store(block=7)
    assert len(first) == 6  # 3*2

    # confirm DB state
    liq_db = cache.latest_liquidity()
    assert len(liq_db) == 6

    # values & keys correct?
    expected = _expected_aggregated()
    for snap in liq_db:
        key = (snap.wallet_hotkey, snap.subnet_id, snap.block_height)
        assert snap.usd_value == expected[key]

    # 2️⃣  second identical run → dedup
    second = fetcher.fetch_and_store(block=7)
    assert second == []  # no duplicates persisted
    assert len(cache.latest_liquidity()) == 6  # still six rows

    # 3️⃣  cache.liquidity mapping populated & correct
    assert hasattr(cache, "liquidity")
    for (ck, subnet, _), usd_val in expected.items():
        uid = fetcher._coldkey_to_uid(ck, subnet)  # type: ignore[attr-defined]
        assert math.isclose(cache.liquidity[subnet][uid], usd_val, abs_tol=1e-9)


