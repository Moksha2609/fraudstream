"""Runtime configuration, loaded from environment variables prefixed with FS_."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FS_", env_file=".env", extra="ignore")

    # --- queue ---------------------------------------------------------
    queue_backend: Literal["memory", "redis", "kafka"] = "redis"
    redis_url: str = "redis://localhost:6379/0"
    kafka_bootstrap: str = "localhost:9092"
    tx_topic: str = "transactions"
    alert_topic: str = "alerts"
    consumer_group: str = "scorer"
    consumer_name: str = "scorer-1"
    # how many messages a consumer pulls per read
    consume_batch: int = 64

    # --- model ---------------------------------------------------------
    model_dir: str = "../models"
    model_name: str = "fraud_xgb"
    # if set, overrides the threshold stored in the model metadata
    alert_threshold: float | None = None

    # --- storage -------------------------------------------------------
    database_url: str = "sqlite:///./fraudstream.db"

    # --- drift ---------------------------------------------------------
    drift_window: int = 5000
    drift_psi_warn: float = 0.1
    drift_psi_alert: float = 0.2

    # --- api -----------------------------------------------------------
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "*"
    # run the queue consumer inside the API process (single-process demo / small deployments)
    run_consumer: bool = False
    log_level: str = "info"


@lru_cache
def get_settings() -> Settings:
    return Settings()
