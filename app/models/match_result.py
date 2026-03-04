from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, Numeric, SmallInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MatchResult(Base):
    __tablename__ = "match_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[int] = mapped_column(Integer, ForeignKey("members.id"), nullable=False)
    division_id: Mapped[int] = mapped_column(Integer, ForeignKey("divisions.id"), nullable=False)
    match_name: Mapped[str] = mapped_column(String(200), nullable=False)
    match_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    match_level: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    placement: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_competitors: Mapped[int | None] = mapped_column(Integer, nullable=True)
    percent_finish: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_match_results_member_id", "member_id"),
        Index("ix_match_results_match_date", "match_date"),
    )
