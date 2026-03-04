from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ClassifierResult(Base):
    __tablename__ = "classifier_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[int] = mapped_column(Integer, ForeignKey("members.id"), nullable=False)
    division_id: Mapped[int] = mapped_column(Integer, ForeignKey("divisions.id"), nullable=False)
    classifier_number: Mapped[str] = mapped_column(String(10), nullable=False)
    classifier_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    match_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    match_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    hit_factor: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    points: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    percentage: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    classification_at_time: Mapped[str | None] = mapped_column(String(2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_classifier_results_member_id", "member_id"),
        Index("ix_classifier_results_match_date", "match_date"),
    )
