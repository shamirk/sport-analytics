from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CurrentClassification(Base):
    __tablename__ = "current_classifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[int] = mapped_column(Integer, ForeignKey("members.id"), nullable=False)
    division_id: Mapped[int] = mapped_column(Integer, ForeignKey("divisions.id"), nullable=False)
    classification_class: Mapped[str] = mapped_column(String(2), nullable=False)
    percentage: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_current_classifications_member_id", "member_id"),
    )
