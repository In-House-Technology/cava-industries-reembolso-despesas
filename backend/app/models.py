"""App-owned timestamps and portable UUIDs; inherit BaseModel for every table."""
from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Date, DateTime, Index, String, Uuid, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


CATEGORIES = ("Alimentação", "Transporte", "Hospedagem", "Combustível", "Material", "Outros")
STATUSES = ("pendente", "aprovada", "recusada")


def utcnow():
    return datetime.now(timezone.utc)


def epoch(value):
    # SQLite returns naive datetimes; all timestamps in this app represent UTC.
    return int((value if value.tzinfo else value.replace(tzinfo=timezone.utc)).timestamp())


class Base(DeclarativeBase):
    pass


class BaseModel:
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at_epoch: Mapped[int] = mapped_column(BigInteger)
    modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    modified_at_epoch: Mapped[int] = mapped_column(BigInteger)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at_epoch: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class Expense(BaseModel, Base):
    """One expense someone paid and wants reimbursed. Amounts are integer centavos (BRL)."""
    __tablename__ = "expenses"
    __table_args__ = (
        Index("expenses_requester_created", "requester_email", "created_at"),
        Index("expenses_status_created", "status", "created_at"),
    )

    requester_email: Mapped[str] = mapped_column(String(320))
    requester_name: Mapped[str] = mapped_column(String(200))
    expense_date: Mapped[date] = mapped_column(Date)
    description: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(40))
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pendente")
    decided_by_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    decided_by_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at_epoch: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)


@event.listens_for(Base, "before_insert", propagate=True)
def inserted(mapper, connection, target):
    if isinstance(target, BaseModel):
        target.created_at = target.modified_at = utcnow()
        target.created_at_epoch = target.modified_at_epoch = epoch(target.created_at)
        target.deleted_at_epoch = epoch(target.deleted_at) if target.deleted_at else None


@event.listens_for(Base, "before_update", propagate=True)
def updated(mapper, connection, target):
    if isinstance(target, BaseModel):
        target.modified_at = utcnow()
        target.modified_at_epoch = epoch(target.modified_at)
        target.deleted_at_epoch = epoch(target.deleted_at) if target.deleted_at else None
    if isinstance(target, Expense):
        target.decided_at_epoch = epoch(target.decided_at) if target.decided_at else None
