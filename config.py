"""
Global configuration entry‑point.

▪ Loads environment variables from `.env` (if present)
▪ Exposes a single singleton `settings` object
▪ Keeps legacy constant names so existing code keeps working
"""

from __future__ import annotations

import os
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from pydantic import AnyUrl, Field, validator
from pydantic_settings import BaseSettings

# ──────────────────────────────────────────────────────────────
# 0. Load .env early so that pydantic can pick up the variables
# ──────────────────────────────────────────────────────────────
load_dotenv()

DEFAULT_BITTENSOR_NETWORK = "finney"
DEFAULT_NETUID = 66
DECIMALS = 10**9
SAMPLE_POINTS = 10

# ── Validator specific  ───────────────────────────────────────────────
VOTE_API_ENDPOINT: AnyUrl | str = Field(
    "TODO",    # ← To be filled
    env="VOTE_API_ENDPOINT",
)

# ──────────────────────────────────────────────────────────────
# 1. Settings object (use everywhere instead of os.getenv)
# ──────────────────────────────────────────────────────────────
class _Settings(BaseSettings):
    # --- General process switches ------------------------------------------------
    LOG_LEVEL: str = Field("INFO", env="LOG_LEVEL")  # DEBUG / INFO / WARNING / ERROR
    PROMETHEUS_PORT: int = Field(8000, env="PROMETHEUS_PORT")
    JSON_LOGS: bool = Field(False, env="JSON_LOGS")

    # --- Storage -----------------------------------------------------------------
    DB_URI: str = Field("sqlite:///./oceans_cache.db", env="DB_URI")

    # --- Bittensor / Subtensor network ------------------------------------------
    BITTENSOR_NETWORK: str = Field("finney", env="BITTENSOR_NETWORK")
    SUBTENSOR_RPC: str = Field("wss://finney.subtensor.network", env="SUBTENSOR_RPC")
    DEFAULT_NETUID: int = Field(66, env="DEFAULT_NETUID")

    # --- Validator specific ------------------------------------------------------
    VOTE_API_ENDPOINT: AnyUrl | str = Field(
        "https://api.oceans66.com/v1", env="VOTE_API_ENDPOINT"
    )
    VOTE_POLL_INTERVAL: int = Field(
        30, env="VOTE_POLL_INTERVAL"
    )  # seconds between polling UI
    LIQUIDITY_REFRESH_BLOCKS: int = Field(
        1, env="LIQUIDITY_REFRESH_BLOCKS"
    )  # how many chain blocks between fetches
    EPOCH_SECONDS: int = Field(600, env="EPOCH_SECONDS")  # fallback if chain epoch unavailable
    MAX_CONCURRENCY: int = Field(5, env="MAX_CONCURRENCY")  # rpc calls in parallel

    # --- Wallet / hotkey ---------------------------------------------------------
    WALLET_NAME: str = Field("default", env="WALLET_NAME")
    WALLET_PASSPHRASE: str = Field("TO-BE-FILLED", env="WALLET_PASSPHRASE")
    WALLET_MNEMONIC: str = Field("TO-BE-FILLED", env="WALLET_MNEMONIC")

    # --- Alerts ------------------------------------------------------------------
    DISCORD_WEBHOOK_URL: Optional[AnyUrl] = Field(None, env="DISCORD_WEBHOOK_URL")

    # pydantic settings
    class Config:
        env_file = ".env"
        case_sensitive = False

    # helpful computed values -----------------------------------------------------
    @property
    def is_prod(self) -> bool:
        return self.BITTENSOR_NETWORK.lower() in {"mainnet", "main"}

    @validator("LOG_LEVEL")
    def _validate_log_level(cls, v: str) -> str:  # noqa: N805
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        v_up = v.upper()
        if v_up not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {allowed}")
        return v_up


@lru_cache(maxsize=1)
def get_settings() -> _Settings:
    """Singleton accessor – import this everywhere."""
    return _Settings()


# instantiate once for module‑level (legacy code expects variables, not a class)
settings = get_settings()
