"""
Unit‑tests for ``validator.state_cache.StateCache``.

A fresh in‑memory SQLite database is spun up for every test function so
no real data are touched.  The production code is exercised unchanged by
monkey‑patching the SQLAlchemy `SessionLocal` used by both
`storage.models` and `validator.state_cache`.

Run with:
    $ pytest
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Dict, Generator, List

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# --------------------------------------------------------------------------- #
# Imports from the project under test
# --------------------------------------------------------------------------- #
from validator import state_cache as sc            # module under test
from storage import models as sm                   # ORM models we persist


# --------------------------------------------------------------------------- #
# Pytest fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="function")
def cache(monkeypatch) -> Generator[sc.StateCache, None, None]:
    """
    Provide a `StateCache` instance backed by an *ephemeral* in‑memory
    database.  Each test gets a pristine schema.
    """
    # 1. Build engine / session‑factory for SQLite ‑ memory
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    TestSessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    # 2. Create tables on that engine
    sm.Base.metadata.create_all(engine)

    # 3. Monkey‑patch SessionLocal in both modules
    monkeypatch.setattr(sm, "SessionLocal", TestSessionLocal, raising=True)
    monkeypatch.setattr(sc, "SessionLocal", TestSessionLocal, raising=True)

    # 4. No need for init_db() – tables already exist
    monkeypatch.setattr(sc, "init_db", lambda: None, raising=True)

    # 5. Yield wired cache
    _cache = sc.StateCache()
    yield _cache

    # 6. Clean‑up
    engine.dispose()


# --------------------------------------------------------------------------- #
# Helper builders – keep test bodies concise
# --------------------------------------------------------------------------- #
def _vote_snapshot(
    voter_hotkey: str,
    block_height: int,
    weights: Dict[int, float] | None = None,
) -> sm.VoteSnapshot:
    """
    Build a `VoteSnapshot` matching the real schema
    (`voter_hotkey`, `block_height`, `weights`, `ts`).
    """
    if weights is None:
        weights = {i: 1 / 128 for i in range(1, 129)}

    return sm.VoteSnapshot(
        voter_hotkey=voter_hotkey,
        block_height=block_height,
        weights=weights,
        # We *could* omit ts – SQLAlchemy default inserts utcnow – but
        # giving one makes test intent explicit.
        ts=datetime.now(timezone.utc),
    )


def _liquidity_snapshot(
    wallet_hotkey: str,
    subnet_id: int,
    block_height: int,
    usd_value: float,
) -> sm.LiquiditySnapshot:
    """
    Build a `LiquiditySnapshot` with correct column names (`usd_value`, `ts`).
    """
    return sm.LiquiditySnapshot(
        wallet_hotkey=wallet_hotkey,
        subnet_id=subnet_id,
        usd_value=usd_value,
        block_height=block_height,
        ts=datetime.now(timezone.utc),
    )


# --------------------------------------------------------------------------- #
# Tests – Votes
# --------------------------------------------------------------------------- #
def test_persist_and_fetch_votes(cache: sc.StateCache) -> None:
    """
    • `persist_votes` writes objects.
    • `latest_votes` returns them ordered by block‑height DESC, id DESC.
    """
    v1 = _vote_snapshot("hk-A", 10)
    v2 = _vote_snapshot("hk-B", 11)

    cache.persist_votes([v1, v2])

    latest = cache.latest_votes()
    # Expect block‑height 11 first
    assert [v.block_height for v in latest] == [11, 10]
    assert latest[0].voter_hotkey == "hk-B"
    assert latest[1].voter_hotkey == "hk-A"

    # sanity on weights – 128 entries summing to 1
    for v in latest:
        assert len(v.weights) == 128
        assert math.isclose(sum(v.weights.values()), 1.0, abs_tol=1e-12)


def test_votes_changed(cache: sc.StateCache) -> None:
    """
    `votes_changed` should report *True* for unseen (hotkey, height)
    pairs and *False* once a matching snapshot exists.
    """
    hk, bh = "hk-C", 99

    assert cache.votes_changed(bh, hk) is True

    cache.persist_votes([_vote_snapshot(hk, bh)])

    assert cache.votes_changed(bh, hk) is False


# --------------------------------------------------------------------------- #
# Tests – Liquidity
# --------------------------------------------------------------------------- #
def test_persist_and_fetch_liquidity(cache: sc.StateCache) -> None:
    """
    Mirrors the vote test: persist two liquidity snapshots and confirm
    retrieval order and stored values.
    """
    l1 = _liquidity_snapshot("wallet‑1", 1, 1234, 1_000.0)
    l2 = _liquidity_snapshot("wallet‑2", 2, 1235, 2_000.0)

    cache.persist_liquidity([l1, l2])

    latest = cache.latest_liquidity()
    assert [l.block_height for l in latest] == [1235, 1234]
    assert latest[0].usd_value == 2_000.0
    assert latest[1].usd_value == 1_000.0
