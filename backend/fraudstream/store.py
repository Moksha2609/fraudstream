"""Persistence for alerts and owner feedback (SQLite by default, Postgres in prod).

Feedback rows are the *new labelled data*: ``export_labeled()`` turns them into
a training frame with the same columns as the public dataset so ``ml/train.py``
can merge them on the next retrain.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pandas as pd
from sqlalchemy import DateTime, Float, Integer, String, Text, create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .features import FEATURE_COLUMNS, V_COLUMNS
from .schemas import Alert, FeedbackLabel


class Base(DeclarativeBase):
    pass


class AlertRow(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tx_id: Mapped[str] = mapped_column(String(64), index=True)
    score: Mapped[float] = mapped_column(Float)
    threshold: Mapped[float] = mapped_column(Float)
    amount: Mapped[float] = mapped_column(Float)
    tx_time: Mapped[float] = mapped_column(Float)
    model_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    features_json: Mapped[str] = mapped_column(Text)
    true_label: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feedback: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    feedback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    feedback_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    def to_schema(self, with_features: bool = True) -> Alert:
        return Alert(
            id=self.id,
            tx_id=self.tx_id,
            score=self.score,
            threshold=self.threshold,
            amount=self.amount,
            tx_time=self.tx_time,
            model_version=self.model_version,
            created_at=self.created_at,
            feedback=self.feedback,  # type: ignore[arg-type]
            feedback_at=self.feedback_at,
            feedback_note=self.feedback_note,
            true_label=self.true_label,
            features=json.loads(self.features_json) if with_features else None,
        )


class AlertStore:
    def __init__(self, database_url: str = "sqlite:///./fraudstream.db"):
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        self.engine = create_engine(database_url, connect_args=connect_args, future=True)
        Base.metadata.create_all(self.engine)
        self._session = sessionmaker(self.engine, expire_on_commit=False)

    def session(self) -> Session:
        return self._session()

    # -- writes -----------------------------------------------------------
    def add_alert(
        self,
        tx_id: str,
        score: float,
        threshold: float,
        amount: float,
        tx_time: float,
        model_version: str,
        features: dict[str, float],
        true_label: int | None = None,
    ) -> Alert:
        row = AlertRow(
            tx_id=tx_id,
            score=score,
            threshold=threshold,
            amount=amount,
            tx_time=tx_time,
            model_version=model_version,
            created_at=datetime.now(UTC),
            features_json=json.dumps(features),
            true_label=true_label,
        )
        with self.session() as s:
            s.add(row)
            s.commit()
            s.refresh(row)
            return row.to_schema()

    def set_feedback(
        self, alert_id: int, label: FeedbackLabel, note: str | None = None
    ) -> Alert | None:
        with self.session() as s:
            row = s.get(AlertRow, alert_id)
            if row is None:
                return None
            row.feedback = label
            row.feedback_at = datetime.now(UTC)
            row.feedback_note = note
            s.commit()
            s.refresh(row)
            return row.to_schema()

    # -- reads ------------------------------------------------------------
    def get_alert(self, alert_id: int) -> Alert | None:
        with self.session() as s:
            row = s.get(AlertRow, alert_id)
            return row.to_schema() if row else None

    def list_alerts(
        self, limit: int = 100, status: str | None = None, since_id: int | None = None
    ) -> list[Alert]:
        stmt = select(AlertRow)
        if status == "unreviewed":
            stmt = stmt.where(AlertRow.feedback.is_(None))
        elif status in ("fraud", "false_alarm"):
            stmt = stmt.where(AlertRow.feedback == status)
        if since_id is not None:
            stmt = stmt.where(AlertRow.id > since_id)
        stmt = stmt.order_by(AlertRow.id.desc()).limit(limit)
        with self.session() as s:
            return [r.to_schema(with_features=False) for r in s.scalars(stmt)]

    def counts(self) -> dict[str, int]:
        with self.session() as s:
            total = s.scalar(select(func.count(AlertRow.id))) or 0
            fraud = (
                s.scalar(select(func.count(AlertRow.id)).where(AlertRow.feedback == "fraud")) or 0
            )
            fa = (
                s.scalar(select(func.count(AlertRow.id)).where(AlertRow.feedback == "false_alarm"))
                or 0
            )
        return {
            "alerts_total": total,
            "fraud": fraud,
            "false_alarm": fa,
            "unreviewed": total - fraud - fa,
        }

    def export_labeled(self) -> pd.DataFrame:
        """Owner-reviewed alerts as a training frame (Time, V1..V28, Amount, Class)."""
        stmt = select(AlertRow).where(AlertRow.feedback.is_not(None)).order_by(AlertRow.id)
        records = []
        with self.session() as s:
            for r in s.scalars(stmt):
                f = json.loads(r.features_json)
                rec = {"Time": r.tx_time, **{c: f.get(c) for c in V_COLUMNS}, "Amount": r.amount}
                rec["Class"] = 1 if r.feedback == "fraud" else 0
                rec["source"] = "feedback"
                rec["tx_id"] = r.tx_id
                records.append(rec)
        cols = ["Time", *V_COLUMNS, "Amount", "Class", "source", "tx_id"]
        return pd.DataFrame.from_records(records, columns=cols)


__all__ = ["AlertStore", "AlertRow", "FEATURE_COLUMNS"]
