from datetime import date, datetime, timedelta, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import aliased
from sqlalchemy.pool import StaticPool

from app import db, main, models
from app.models import Base, Expense

DATA = {"expense_date": date(2026, 1, 1), "description": "Almoço com cliente", "category": "Alimentação",
        "amount_cents": 12345, "notes": None}


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with db.AppSession(engine) as session:
        yield session
    engine.dispose()


def test_insert_update_and_soft_delete_pairs(session, monkeypatch):
    now = datetime(2026, 1, 1, 12, 0, 0, 123456, tzinfo=timezone.utc)
    monkeypatch.setattr(models, "utcnow", lambda: now)
    expense = db.create_expense(session, "alice@example.com", "Alice", DATA)
    assert isinstance(expense.id, UUID)
    assert expense.status == "pendente"
    assert expense.created_at.replace(tzinfo=timezone.utc) == now
    assert expense.modified_at == expense.created_at
    assert expense.created_at_epoch == expense.modified_at_epoch == int(now.timestamp())
    assert expense.deleted_at is expense.deleted_at_epoch is None

    now += timedelta(seconds=5)
    expense.description = "Changed"
    session.commit()
    session.refresh(expense)
    assert expense.modified_at.replace(tzinfo=timezone.utc) == now
    assert expense.modified_at_epoch == int(now.timestamp())
    assert expense.created_at_epoch == int(now.timestamp()) - 5

    now += timedelta(seconds=5)
    monkeypatch.setattr(db, "utcnow", lambda: now)
    assert db.cancel_expense(session, "bob@example.com", expense.id) == "not_found"
    assert db.cancel_expense(session, "alice@example.com", expense.id) == "ok"
    assert db.list_my_expenses(session, "alice@example.com") == []
    assert db.list_pending(session) == []
    assert session.scalars(select(Expense)).all() == []
    assert session.scalars(select(aliased(Expense))).all() == []
    assert db.cancel_expense(session, "alice@example.com", expense.id) == "not_found"
    deleted = session.scalar(select(Expense).execution_options(include_deleted=True))
    assert deleted.deleted_at.replace(tzinfo=timezone.utc) == now
    assert deleted.deleted_at_epoch == deleted.modified_at_epoch == int(now.timestamp())


def test_decide_rules(session, monkeypatch):
    now = datetime(2026, 2, 1, 9, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(db, "utcnow", lambda: now)
    expense = db.create_expense(session, "alice@example.com", "Alice", DATA)
    other = db.create_expense(session, "carol@example.com", "Carol", {**DATA, "amount_cents": 500})
    assert [e.id for e in db.list_pending(session)] == [expense.id, other.id]

    outcome, _ = db.decide_expense(session, "Alice@example.com", "Alice", expense.id, "aprovada", None)
    assert outcome == "own_expense"
    outcome, decided = db.decide_expense(session, "boss@example.com", "Boss", expense.id, "aprovada", None)
    assert outcome == "ok" and decided.status == "aprovada"
    assert decided.decided_by_email == "boss@example.com" and decided.decided_by_name == "Boss"
    assert decided.decided_at_epoch == int(now.timestamp())
    assert db.decide_expense(session, "boss@example.com", "Boss", expense.id, "recusada", "x")[0] == "already_decided"
    assert db.cancel_expense(session, "alice@example.com", expense.id) == "already_decided"

    outcome, declined = db.decide_expense(session, "boss@example.com", "Boss", other.id, "recusada", "Sem nota fiscal")
    assert outcome == "ok" and declined.status == "recusada" and declined.decision_note == "Sem nota fiscal"
    assert db.list_pending(session) == []
    assert [e.status for e in db.list_my_expenses(session, "alice@example.com", "aprovada")] == ["aprovada"]
    assert db.list_my_expenses(session, "alice@example.com", "recusada") == []
    assert db.decide_expense(session, "boss@example.com", "Boss", UUID(int=1), "aprovada", None)[0] == "not_found"


def test_routes_end_to_end_with_isolation(session):
    main.app.dependency_overrides[db.get_session] = lambda: session
    try:
        with TestClient(main.app) as client:
            alice = {"X-authentik-email": "alice@example.com", "X-authentik-name": "Alice"}
            bob = {"X-authentik-email": "bob@example.com"}
            boss = {"X-authentik-email": "boss@example.com", "X-authentik-name": "Boss", "X-authentik-groups": "ihc-users|ihc-admins"}
            body = {"expense_date": date.today().isoformat(), "description": "Táxi", "category": "Transporte",
                    "amount_cents": 3200, "notes": "Reunião no cliente"}
            response = client.post("/api/expenses", headers=alice, json=body)
            assert response.status_code == 201
            expense = response.json()
            assert expense["status"] == "pendente" and expense["requester_name"] == "Alice"
            assert client.get("/api/expenses", headers=alice).json()["expenses"][0]["id"] == expense["id"]
            assert client.get("/api/expenses", headers=bob).json() == {"expenses": []}
            assert client.delete("/api/expenses/" + expense["id"], headers=bob).status_code == 404

            assert [e["id"] for e in client.get("/api/approvals", headers=boss).json()["expenses"]] == [expense["id"]]
            declined = client.post("/api/approvals/" + expense["id"], headers=boss,
                                   json={"decision": "recusada", "note": " Falta comprovante "})
            assert declined.status_code == 200
            assert declined.json()["status"] == "recusada" and declined.json()["decision_note"] == "Falta comprovante"
            assert client.post("/api/approvals/" + expense["id"], headers=boss, json={"decision": "aprovada"}).status_code == 409
            assert client.delete("/api/expenses/" + expense["id"], headers=alice).status_code == 409
            mine = client.get("/api/expenses", headers=alice).json()["expenses"][0]
            assert mine["status"] == "recusada" and mine["decided_by_name"] == "Boss"

            own = client.post("/api/expenses", headers=boss, json=body).json()
            assert client.post("/api/approvals/" + own["id"], headers=boss, json={"decision": "aprovada"}).status_code == 403
            assert client.delete("/api/expenses/" + own["id"], headers=boss).status_code == 204
            assert client.get("/api/approvals", headers=boss).json() == {"expenses": []}
            assert client.post("/api/approvals/" + own["id"], headers=boss, json={"decision": "aprovada"}).status_code == 404
    finally:
        main.app.dependency_overrides.clear()


@pytest.mark.parametrize("url", ["postgres://user:pass@localhost/db", "postgresql://user:pass@localhost/db", "postgresql+psycopg://user:pass@localhost/db", "sqlite://"])
def test_database_url_driver(monkeypatch, url):
    monkeypatch.setenv("DATABASE_URL", url)
    result = db.database_url()
    assert result.drivername == ("sqlite" if url == "sqlite://" else "postgresql+psycopg")
    if result.drivername != "sqlite":
        assert result.username == "user" and result.password == "pass"
