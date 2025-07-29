"""
Unit‑tests for validator.vote_fetcher.VoteFetcher

Run with:
    $ pytest
"""
from __future__ import annotations

import math
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from validator.vote_fetcher import VoteFetcher

# ---------------------------------------------------------------------------#
# Lightweight test doubles
# ---------------------------------------------------------------------------#
TOL = 1e-12  # numerical tolerance for float comparisons


class DummyVote:
    """
    Minimal stand‑in for the real pydantic ``Vote`` model – provides only
    the attributes used by VoteFetcher.
    """

    def __init__(self, voter_hotkey: str, subnet_id: int, weight: float, block: int):
        self.voter_hotkey = voter_hotkey
        self.subnet_id = subnet_id
        self.weight = weight
        self.block_height = block
        # The real model also has a `weights` mapping; include a 1‑entry
        # dict so VoteFetcher can iterate over it when building snapshots
        self.weights = {subnet_id: weight}


class DummyCache:
    """
    Bare‑bones in‑memory replacement for StateCache.
    """

    def __init__(self) -> None:
        self.subnet_weights: dict[int, float] | None = None
        self.latest_votes = None      # set by VoteFetcher
        self.persisted: list | None = None  # captures what was persisted

    # --- APIs exercised by VoteFetcher ----------------------------------#
    @contextmanager
    def _session(self):  # pylint: disable=protected-access, missing‑docstring
        yield SimpleNamespace()  # VoteFetcher never uses the session

    def votes_changed(self, block_height: int, voter_hotkey: str) -> bool:  # noqa: D401
        # Pretend every (height, hotkey) pair is *new* so snapshots are saved
        return True

    def persist_votes(self, snapshots) -> None:  # noqa: D401
        # Capture for later assertions
        self.persisted = snapshots


# ---------------------------------------------------------------------------#
# Helper to assemble a VoteFetcher with injected doubles
# ---------------------------------------------------------------------------#
def make_fetcher(votes):
    cache = DummyCache()

    api_client = Mock()          # stand‑in for VoteAPIClient
    api_client.get_latest_votes.return_value = votes

    fetcher = VoteFetcher(cache, api_client=api_client)
    return fetcher, cache, api_client


# ---------------------------------------------------------------------------#
# Tests
# ---------------------------------------------------------------------------#
def test_fetch_and_store_normalises_and_persists() -> None:
    """
    • Duplicate subnets across votes are aggregated  
    • Weights are normalised so Σ = 1  
    • New VoteSnapshot rows are persisted via StateCache.persist_votes
    """
    votes = [
        DummyVote("hk‑1", 1, 0.30, 123),
        DummyVote("hk‑2", 1, 0.70, 123),   # duplicates subnet‑1 (should aggregate)
        DummyVote("hk‑3", 2, 1.00, 123),
    ]

    # Patch the ORM model so we don't touch the real DB layer
    with patch("validator.vote_fetcher.VoteSnapshot", autospec=True) as MockVS:
        fetcher, cache, _ = make_fetcher(votes)
        weights = fetcher.fetch_and_store()

        # ── weight checks ────────────────────────────────────────────────
        assert set(weights) == {1, 2}
        assert math.isclose(weights[1], 0.5, abs_tol=TOL)
        assert math.isclose(weights[2], 0.5, abs_tol=TOL)
        assert math.isclose(sum(weights.values()), 1.0, abs_tol=TOL)

        # ── cache side‑effects ───────────────────────────────────────────
        assert cache.subnet_weights == weights
        assert cache.latest_votes is not None

        # ── persistence ─────────────────────────────────────────────────
        # VoteFetcher should have built one VoteSnapshot per *input* vote
        # (it defers duplicate‑row detection to `votes_changed`)
        assert MockVS.call_count == len(votes)
        assert cache.persisted is not None
        assert len(cache.persisted) == len(votes)


def test_fetch_and_store_empty_vote_list() -> None:
    """
    Empty remote payload → empty weights mapping and *no* persistence.
    """
    with patch("validator.vote_fetcher.VoteSnapshot"):
        fetcher, cache, _ = make_fetcher([])
        weights = fetcher.fetch_and_store()

        assert weights == {}
        assert cache.subnet_weights == {}
        assert cache.persisted is None  # persist_votes never called


def test_fetch_and_store_zero_total_weight() -> None:
    """
    Votes whose raw weights sum to zero → normaliser empties the mapping.
    """
    votes = [
        DummyVote("hk‑A", 4, 0.0, 222),
        DummyVote("hk‑B", 5, 0.0, 222),
    ]

    with patch("validator.vote_fetcher.VoteSnapshot"):
        fetcher, cache, _ = make_fetcher(votes)
        weights = fetcher.fetch_and_store()

        assert weights == {}
        assert cache.subnet_weights == {}
