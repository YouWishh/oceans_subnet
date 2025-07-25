"""
Local persistence layer for snapshots.

▪ Uses SQLite (default URI comes from settings.DB_URI)
▪ SQLAlchemy ORM keeps things simple & type‑safe
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    create_engine,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, sessionmaker

from config import get_settings

settings = get_settings()

# ──────────────────────────────────────────────────────────────
# 1. Engine & Session factory
# ──────────────────────────────────────────────────────────────
_ENGINE = create_engine(
    settings.DB_URI,
    echo=False,
    connect_args={"check_same_thread": False}  # required for SQLite multithread
)
SessionLocal: sessionmaker[Session] = sessionmaker(
    autocommit=False, autoflush=False, bind=_ENGINE
)

# ──────────────────────────────────────────────────────────────
# 2. Declarative Base
# ──────────────────────────────────────────────────────────────
Base = declarative_base()


# ──────────────────────────────────────────────────────────────
# 3. ORM models
# ──────────────────────────────────────────────────────────────
class VoteSnapshot(Base):
    """
    One snapshot of the full subnet‑weights vector produced by α‑Stake voting.
    """

    __tablename__ = "vote_snapshots"

    id: int = Column(Integer, primary_key=True, index=True)
    block_height: int = Column(Integer, nullable=False, index=True)
    voter_hotkey: str = Column(String(64), nullable=False, index=True)
    weights: Any = Column(JSON, nullable=False)  # {subnet_id: weight, …}
    ts: _dt.datetime = Column(
        DateTime(timezone=True), default=_dt.datetime.utcnow, nullable=False, index=True
    )


class LiquiditySnapshot(Base):
    """
    Liquidity provided by one miner in one subnet at a given block.
    """

    __tablename__ = "liquidity_snapshots"

    id: int = Column(Integer, primary_key=True, index=True)
    wallet_hotkey: str = Column(String(64), nullable=False, index=True)
    subnet_id: int = Column(Integer, nullable=False, index=True)
    usd_value: float = Column(Float, nullable=False)
    block_height: int = Column(Integer, nullable=False, index=True)
    ts: _dt.datetime = Column(
        DateTime(timezone=True), default=_dt.datetime.utcnow, nullable=False, index=True
    )


# ──────────────────────────────────────────────────────────────
# 4. Helpers
# ──────────────────────────────────────────────────────────────
def init_db() -> None:
    """
    Call once on application start‑up (validator & miner entry points).
    Ensures schema exists.
    """
    Base.metadata.create_all(bind=_ENGINE)


def get_session() -> Session:
    """
    Dependency injection helper – use via `with get_session() as db: …`
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
