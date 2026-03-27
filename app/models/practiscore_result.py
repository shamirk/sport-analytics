from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PractiscoreResult(Base):
    __tablename__ = "practiscore_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(Integer, ForeignKey("practiscore_matches.id"), nullable=False)
    shooter_name: Mapped[str] = mapped_column(String(200), nullable=False)
    member_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    division: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    classification: Mapped[str | None] = mapped_column(String(5), nullable=True)
    total_points: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    total_time: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    percent_of_winner: Mapped[float | None] = mapped_column(Numeric(7, 4), nullable=True)
    placement: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_queried_member: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_practiscore_results_match_id", "match_id"),
        Index("ix_practiscore_results_member_number", "member_number"),
    )
