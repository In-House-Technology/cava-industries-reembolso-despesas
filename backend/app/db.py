"""Shared Postgres sessions; pass the person to every data operation."""
import os
from functools import lru_cache

from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker, with_loader_criteria

from .models import BaseModel, Expense, utcnow


def database_url():
    url = make_url(os.getenv("DATABASE_URL", "postgresql+psycopg://"))
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+psycopg")
    return url


class AppSession(Session):
    """Use execution_options(include_deleted=True) for explicit administrative reads."""


@event.listens_for(AppSession, "do_orm_execute")
def active_rows(state):
    if state.is_select and not state.execution_options.get("include_deleted", False):
        state.statement = state.statement.options(
            with_loader_criteria(BaseModel, lambda model: model.deleted_at.is_(None), include_aliases=True)
        )


@lru_cache
def session_factory():
    return sessionmaker(bind=create_engine(database_url(), pool_pre_ping=True), class_=AppSession)


def get_session():
    with session_factory()() as session:
        yield session


def serialize(expense: Expense):
    return {
        "id": str(expense.id),
        "requester_email": expense.requester_email,
        "requester_name": expense.requester_name,
        "expense_date": expense.expense_date.isoformat(),
        "description": expense.description,
        "category": expense.category,
        "amount_cents": expense.amount_cents,
        "notes": expense.notes,
        "status": expense.status,
        "decided_by_email": expense.decided_by_email,
        "decided_by_name": expense.decided_by_name,
        "decided_at": expense.decided_at.isoformat() if expense.decided_at else None,
        "decision_note": expense.decision_note,
        "created_at": expense.created_at.isoformat(),
    }


def list_my_expenses(session: Session, email, status=None):
    query = select(Expense).where(Expense.requester_email == email)
    if status:
        query = query.where(Expense.status == status)
    return session.scalars(query.order_by(Expense.created_at.desc(), Expense.id)).all()


def list_pending(session: Session):
    """Approvers' queue, oldest first so nothing waits forever."""
    query = select(Expense).where(Expense.status == "pendente")
    return session.scalars(query.order_by(Expense.created_at, Expense.id)).all()


def create_expense(session: Session, email, name, data):
    expense = Expense(requester_email=email, requester_name=name or email, status="pendente", **data)
    session.add(expense)
    session.commit()
    session.refresh(expense)
    return expense


def cancel_expense(session: Session, email, expense_id):
    """The requester withdraws a pending expense (soft delete). Decided expenses stay on record."""
    expense = session.scalar(select(Expense).where(Expense.id == expense_id, Expense.requester_email == email))
    if expense is None:
        return "not_found"
    if expense.status != "pendente":
        return "already_decided"
    expense.deleted_at = utcnow()
    session.commit()
    return "ok"


def decide_expense(session: Session, email, name, expense_id, decision, note):
    """Approve or decline a pending expense. Returns (outcome, expense)."""
    expense = session.scalar(select(Expense).where(Expense.id == expense_id))
    if expense is None:
        return "not_found", None
    if expense.requester_email.lower() == email.lower():
        return "own_expense", expense
    if expense.status != "pendente":
        return "already_decided", expense
    expense.status = decision
    expense.decided_by_email = email
    expense.decided_by_name = name or email
    expense.decided_at = utcnow()
    expense.decision_note = note
    session.commit()
    session.refresh(expense)
    return "ok", expense
