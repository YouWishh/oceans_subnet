"""
Thin abstraction over storage.models to cache / diff validator inputs
across epochs.

Not a heavy data‑store – just enough to avoid recomputing expensive
RPC calls when nothing changed.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from storage.models import (
    LiquiditySnapshot,
    SessionLocal,
    VoteSnapshot,
    init_db,
)

log = logging.getLogger("state_cache")


class StateCache:
    """
    Stateless entry‑point the validator will instantiate once and then
    call every epoch.

    Example:
        cache = StateCache()
        latest_votes = cache.latest_votes()
        cache.persist_votes(new_snapshots)
    """

    def __init__(self) -> None:
        init_db()  # ensure tables exist
        self._session_factory = SessionLocal

    # ──────────────────────────────────────────────────────────
    # Votes
    # ──────────────────────────────────────────────────────────
    def latest_votes(self) -> List[VoteSnapshot]:
        with self._session() as db:
            return (
                db.query(VoteSnapshot)
                .order_by(VoteSnapshot.block_height.desc(), VoteSnapshot.id.desc())
                .all()
            )

    def persist_votes(self, snapshots: List[VoteSnapshot]) -> None:
        if not snapshots:
            return
        with self._session() as db:
            db.bulk_save_objects(snapshots)
            db.commit()
            log.debug("Persisted %s vote snapshots", len(snapshots))

    # ──────────────────────────────────────────────────────────
    # Liquidity
    # ──────────────────────────────────────────────────────────
    def latest_liquidity(self) -> List[LiquiditySnapshot]:
        with self._session() as db:
            return (
                db.query(LiquiditySnapshot)
                .order_by(
                    LiquiditySnapshot.block_height.desc(),
                    LiquiditySnapshot.id.desc(),
                )
                .all()
            )

    def persist_liquidity(self, snapshots: List[LiquiditySnapshot]) -> None:
        if not snapshots:
            return
        with self._session() as db:
            db.bulk_save_objects(snapshots)
            db.commit()
            log.debug("Persisted %s liquidity snapshots", len(snapshots))

    # ──────────────────────────────────────────────────────────
    # Diff helpers (optional utility)
    # ──────────────────────────────────────────────────────────
    def votes_changed(
        self, new_block_height: int, voter_hotkey: str
    ) -> bool:
        """
        Quick check to skip heavy computations when nothing’s changed
        since the last processed epoch.
        """
        with self._session() as db:
            last = (
                db.query(VoteSnapshot)
                .filter(
                    VoteSnapshot.voter_hotkey == voter_hotkey,
                    VoteSnapshot.block_height == new_block_height,
                )
                .first()
            )
            return last is None

    # ──────────────────────────────────────────────────────────
    # Internal
    # ──────────────────────────────────────────────────────────
    def _session(self) -> Session:
        return self._session_factory()
