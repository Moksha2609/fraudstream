"""Pydantic models shared by the API, the consumer and the replay producer."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FeedbackLabel = Literal["fraud", "false_alarm"]


class Transaction(BaseModel):
    """One card transaction in the shape of the Kaggle/ULB credit-card dataset.

    `time` is seconds elapsed since the start of the stream; `v` holds the 28
    PCA-anonymised features V1..V28 from the dataset; `amount` is the amount.
    `true_label` is only present when replaying labelled data and is NEVER used
    for scoring - it lets the dashboard measure precision after the fact.
    """

    model_config = ConfigDict(extra="ignore")

    tx_id: str
    time: float = Field(ge=0)
    amount: float = Field(ge=0)
    v: list[float] = Field(min_length=28, max_length=28, description="V1..V28")
    true_label: int | None = Field(default=None, ge=0, le=1)
    merchant: str | None = None
    card_id: str | None = None


class ScoreResponse(BaseModel):
    tx_id: str
    score: float
    is_alert: bool
    threshold: float
    model_version: str
    latency_ms: float


class Alert(BaseModel):
    id: int
    tx_id: str
    score: float
    threshold: float
    amount: float
    tx_time: float
    model_version: str
    created_at: datetime
    feedback: FeedbackLabel | None = None
    feedback_at: datetime | None = None
    feedback_note: str | None = None
    true_label: int | None = None
    features: dict[str, float] | None = None


class FeedbackRequest(BaseModel):
    label: FeedbackLabel
    note: str | None = None


class DriftFeature(BaseModel):
    name: str
    psi: float
    status: Literal["ok", "warn", "drift"]


class DriftReport(BaseModel):
    window_size: int
    observed: int
    status: Literal["ok", "warn", "drift", "insufficient_data"]
    score_psi: float | None
    alert_rate: float | None
    reference_alert_rate: float | None
    features: list[DriftFeature]
    drifted_features: list[str]
    computed_at: datetime


class Stats(BaseModel):
    scored_total: int
    alerts_total: int
    alert_rate: float
    feedback_fraud: int
    feedback_false_alarm: int
    unreviewed: int
    precision_on_feedback: float | None
    throughput_per_s: float
    latency_p50_ms: float | None
    latency_p99_ms: float | None
    model_version: str
    threshold: float
