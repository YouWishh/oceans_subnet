"""
Unit‑tests for VoteAPIClient offline (temporal) mode.

Run with:
    $ pytest
"""
from datetime import datetime
import math

import pytest

from api.client import VoteAPIClient

# ---------------------------------------------------------------------------#
# Expected hard‑coded values (mirror the constants in vote_api_client.py)
# ---------------------------------------------------------------------------#
EXPECTED_HOTKEYS = {
    "5HdK1zyMbMoq1NM2sDL2Len9h2CsmBcVbrFthePccMN5R8jU",
    "5CdG8JDyzBPvXD1PM3ctdVmk3DbC52aTmYbQNezasVUXsn66",
    "5CsvRJXuR955WojnGMdok1hbhffZyB4N5ocrv82f3p5A2zVp",
    "5ExiuLNctkEUL5xMijujmAdhJGdzb5d6vxdzLdjpH3MLNovF",
}
EXPECTED_BLOCK_HEIGHT = 6_073_385

# Inactive / unknown subnets to be excluded from weighting
INACTIVE_SUBNETS = {
    15, 46, 67, 69, 74, 78, 82, 83, 95, 100,
    101, 104, 110, 112, 115, 116, 117, 118, 119, 120,
}
ACTIVE_SUBNETS = {i for i in range(1, 129)} - INACTIVE_SUBNETS  # 108 subnets
EXPECTED_WEIGHT = 1 / len(ACTIVE_SUBNETS)                       # 1/108
WEIGHT_SUM = 1.0
TOL = 1e-12  # numerical tolerance for float sums


# ---------------------------------------------------------------------------#
# Tests
# ---------------------------------------------------------------------------#
def test_temporal_votes_returned_when_endpoint_is_todo() -> None:
    """
    • The client should detect the sentinel ``"TODO"`` and *not* make any
      network requests.
    • It must return four deterministic Vote objects whose values match
      the hard‑coded specification in offline (temporal) mode.
    """
    with VoteAPIClient(base_url="TODO") as client:
        votes = client.get_latest_votes()

    # ---- collection‑level assertions ------------------------------------#
    assert len(votes) == 4, "expected exactly four temporal votes"

    # ---- per‑vote assertions --------------------------------------------#
    for v in votes:
        # hotkey
        assert v.voter_hotkey in EXPECTED_HOTKEYS

        # block height
        assert v.block_height == EXPECTED_BLOCK_HEIGHT

        # weights
        assert set(v.weights.keys()) == ACTIVE_SUBNETS, "weights must cover all active subnets"
        assert all(
            math.isclose(w, EXPECTED_WEIGHT, abs_tol=TOL) for w in v.weights.values()
        ), "each active subnet weight must be 1/108"
        assert math.isclose(
            sum(v.weights.values()), WEIGHT_SUM, abs_tol=TOL
        ), "weights should sum to 1"

        # timestamp (should be a datetime in UTC)
        assert isinstance(v.timestamp, datetime)


@pytest.mark.parametrize("subnet_id", [1, 64, 128])
def test_each_active_weight_is_one_over_108(subnet_id: int) -> None:
    """
    Spot‑check a few *active* subnet IDs to confirm individual weights.
    """
    # Sanity: chosen IDs must be active
    assert subnet_id in ACTIVE_SUBNETS, "selected subnet must be active for this test"

    with VoteAPIClient(base_url="TODO") as client:
        votes = client.get_latest_votes()

    for v in votes:
        assert math.isclose(
            v.weights[subnet_id], EXPECTED_WEIGHT, abs_tol=TOL
        ), f"weight for subnet {subnet_id} should be 1/108"


@pytest.mark.parametrize("subnet_id", sorted(INACTIVE_SUBNETS)[:3])  # test a few
def test_inactive_subnets_are_absent(subnet_id: int) -> None:
    """
    Ensure inactive/unknown subnets are completely omitted from the vote
    vectors.
    """
    with VoteAPIClient(base_url="TODO") as client:
        votes = client.get_latest_votes()

    for v in votes:
        assert subnet_id not in v.weights, f"inactive subnet {subnet_id} should be absent"
