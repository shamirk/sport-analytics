from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, SmallInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PractiscoreMatch(Base):
    __tablename__ = "practiscore_matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[int] = mapped_column(Integer, ForeignKey("members.id"), nullable=False)
    match_name: Mapped[str] = mapped_column(String(300), nullable=False)
    match_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    match_level: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    division: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    practiscore_match_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    total_competitors: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_practiscore_matches_member_id", "member_id"),
        Index("ix_practiscore_matches_match_date", "match_date"),
    )
