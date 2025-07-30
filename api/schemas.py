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

    • `voter_stake` = amount of α (float ≥ 0) held by the voter.  
      The external JSON can supply the field as either
      `"voter_stake"` **or** `"alpha_stake"`.
    """

    voter_hotkey: constr(strip_whitespace=True, min_length=10, max_length=64)
    block_height: conint(ge=0)

    # Amount of stake held by the voter
    voter_stake: float = Field(
        ..., alias="voter_stake", ge=0, description="α‑Stake held by voter"
    )

    weights: Dict[int, float] = Field(
        ...,
        description="Mapping subnet_id ➜ weight (0–1). "
        "Validator will normalise / validate later.",
    )
    timestamp: datetime | None = None  # optional, set by the API

    # ──────────────────────────────────────────────────────────────────
    # Basic sanity checks
    # ──────────────────────────────────────────────────────────────────
    @validator("weights")
    def _weights_non_empty(cls, v: Dict[int, float]):  # noqa: N805
        if not v:
            raise ValueError("weights must not be empty")
        return v

    class Config:  # noqa: D106
        allow_population_by_field_name = True  # permits `voter_stake=` when building objects
