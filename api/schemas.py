"""
Pydantic schemas for the vote API.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict

from pydantic import BaseModel, Field, conint, constr, validator


class Vote(BaseModel):
    """
    One α‑Stake vote entry as served by `/votes/latest`.
    """

    voter_hotkey: constr(strip_whitespace=True, min_length=10, max_length=64)
    block_height: conint(ge=0)
    weights: Dict[int, float] = Field(
        ...,
        description="Mapping subnet_id ➜ weight (0–1). "
        "Validator will normalise / validate later.",
    )
    timestamp: datetime | None = None  # optional, set by the API

    # basic sanity check
    @validator("weights")
    def _weights_non_empty(cls, v: Dict[int, float]):  # noqa: N805
        if not v:
            raise ValueError("weights must not be empty")
        return v
