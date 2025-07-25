"""
Unit‑tests for VoteAPIClient offline (dummy) mode.

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
EXPECTED_WEIGHTS = {i: 1 / 128 for i in range(1, 129)}
WEIGHT_SUM = 1.0
TOL = 1e-12  # numerical tolerance for float sums


# ---------------------------------------------------------------------------#
# Tests
# ---------------------------------------------------------------------------#
def test_dummy_votes_returned_when_endpoint_is_todo() -> None:
    """
    • The client should detect the special sentinel ``"TODO"`` and
      *not* make any network requests.
    • It must instead return four deterministic Vote objects whose
      values match the hard‑coded specification.
    """
    with VoteAPIClient(base_url="TODO") as client:
        votes = client.get_latest_votes()

    # ---- collection‑level assertions ------------------------------------#
    assert len(votes) == 4, "expected exactly four dummy votes"

    # ---- per‑vote assertions --------------------------------------------#
    for v in votes:
        # hotkey
        assert v.voter_hotkey in EXPECTED_HOTKEYS

        # block height
        assert v.block_height == EXPECTED_BLOCK_HEIGHT

        # weights
        assert v.weights == EXPECTED_WEIGHTS
        assert math.isclose(
            sum(v.weights.values()), WEIGHT_SUM, abs_tol=TOL
        ), "weights should sum to 1"

        # timestamp (should be a datetime in UTC)
        assert isinstance(v.timestamp, datetime)


@pytest.mark.parametrize("subnet_id", [1, 64, 128])
def test_each_weight_is_one_over_128(subnet_id: int) -> None:
    """
    Spot‑check a few subnet IDs to confirm individual weights.
    """
    with VoteAPIClient(base_url="TODO") as client:
        votes = client.get_latest_votes()

    for v in votes:
        assert math.isclose(
            v.weights[subnet_id], 1 / 128, abs_tol=TOL
        ), f"weight for subnet {subnet_id} should be 1/128"
