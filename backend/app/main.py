import os
from datetime import date
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exception_handlers import http_exception_handler
from pydantic import BaseModel, Field, field_validator, model_validator

from . import db
from .mcp import actor, require_read
from .models import CATEGORIES, STATUSES
from .theme import theme


app = FastAPI(title="reembolso-despesas")
STATIC = Path(__file__).resolve().parent.parent / "static"
MAX_AMOUNT_CENTS = 100_000_000  # R$ 1.000.000,00


@app.exception_handler(HTTPException)
async def http_error(request: Request, error: HTTPException):
    if request.url.path.startswith("/mcp/"):
        code = {400: "invalid_arguments", 401: "invalid_actor_token", 403: "tool_access_denied", 404: "tool_not_found"}.get(error.status_code, "internal_error")
        return JSONResponse({"error": {"code": code, "message": str(error.detail)}}, status_code=error.status_code)
    return await http_exception_handler(request, error)


def approver_groups():
    raw = os.getenv("APPROVER_GROUPS") or "ihc-admins|reembolso-gestores"
    return {group.strip() for group in raw.split("|") if group.strip()}


def is_approver(groups):
    return bool(approver_groups() & set(groups or []))


def person(request: Request):
    email = request.headers.get("x-authentik-email")
    if email:
        return {"email": email, "name": request.headers.get("x-authentik-name", ""),
                "username": request.headers.get("x-authentik-username", ""),
                "groups": [g for g in request.headers.get("x-authentik-groups", "").split("|") if g]}
    # LOCAL_* is deliberately stripped by platform provisioning. Set only on your laptop.
    if os.getenv("LOCAL_AUTH_MOCK_EMAIL"):
        return {"email": os.environ["LOCAL_AUTH_MOCK_EMAIL"], "name": os.getenv("LOCAL_AUTH_MOCK_NAME", "Local Dev"),
                "username": "local",
                "groups": [g for g in os.getenv("LOCAL_AUTH_MOCK_GROUPS", "").split("|") if g]}
    raise HTTPException(401, detail="Company sign-in required")


def approver(user=Depends(person)):
    if not is_approver(user["groups"]):
        raise HTTPException(403, detail="Somente gestores aprovam ou recusam despesas")
    return user


@app.get("/health")
def health():
    return {"ok": True, "commit": os.getenv("COMMIT_SHA") or os.getenv("GIT_SHA") or "unknown"}


@app.get("/api/me")
def me(user=Depends(person)):
    return {**user, "is_approver": is_approver(user["groups"]), "categories": list(CATEGORIES)}


def clean_text(value, required):
    value = (value or "").strip()
    if required and not value:
        raise ValueError("Campo obrigatório")
    return value or None


class ExpenseInput(BaseModel):
    expense_date: date
    description: str = Field(max_length=200)
    category: str
    amount_cents: int = Field(gt=0, le=MAX_AMOUNT_CENTS)
    notes: str | None = Field(default=None, max_length=1000)

    @field_validator("description")
    @classmethod
    def trim_description(cls, value):
        return clean_text(value, required=True)

    @field_validator("notes")
    @classmethod
    def trim_notes(cls, value):
        return clean_text(value, required=False)

    @field_validator("category")
    @classmethod
    def known_category(cls, value):
        if value not in CATEGORIES:
            raise ValueError("Categoria inválida")
        return value

    @field_validator("expense_date")
    @classmethod
    def not_in_future(cls, value):
        if value > date.today():
            raise ValueError("A data da despesa não pode estar no futuro")
        return value


class DecisionInput(BaseModel):
    decision: Literal["aprovada", "recusada"]
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("note")
    @classmethod
    def trim_note(cls, value):
        return clean_text(value, required=False)

    @model_validator(mode="after")
    def reason_for_decline(self):
        if self.decision == "recusada" and not self.note:
            raise ValueError("Informe o motivo da recusa")
        return self


@app.get("/api/expenses")
def my_expenses(user=Depends(person), session=Depends(db.get_session)):
    return {"expenses": [db.serialize(e) for e in db.list_my_expenses(session, user["email"])]}


@app.post("/api/expenses", status_code=201)
def create_expense(body: ExpenseInput, user=Depends(person), session=Depends(db.get_session)):
    return db.serialize(db.create_expense(session, user["email"], user["name"], body.model_dump()))


@app.delete("/api/expenses/{expense_id}", status_code=204)
def cancel_expense(expense_id: UUID, user=Depends(person), session=Depends(db.get_session)):
    outcome = db.cancel_expense(session, user["email"], expense_id)
    if outcome == "not_found":
        raise HTTPException(404, detail="Despesa não encontrada")
    if outcome == "already_decided":
        raise HTTPException(409, detail="A despesa já foi decidida e não pode ser cancelada")
    return Response(status_code=204)


@app.get("/api/approvals")
def pending(user=Depends(approver), session=Depends(db.get_session)):
    return {"expenses": [db.serialize(e) for e in db.list_pending(session)]}


@app.post("/api/approvals/{expense_id}")
def decide(expense_id: UUID, body: DecisionInput, user=Depends(approver), session=Depends(db.get_session)):
    outcome, expense = db.decide_expense(session, user["email"], user["name"], expense_id, body.decision, body.note)
    if outcome == "not_found":
        raise HTTPException(404, detail="Despesa não encontrada")
    if outcome == "own_expense":
        raise HTTPException(403, detail="Você não pode decidir a sua própria despesa")
    if outcome == "already_decided":
        raise HTTPException(409, detail="Esta despesa já foi decidida")
    return db.serialize(expense)


MCP_TOOLS = {
    "list_my_expenses": {
        "description": "Lista as suas despesas de reembolso com status (pendente, aprovada, recusada), como aparecem no app.",
        "input_schema": {"type": "object", "properties": {"status": {"type": "string", "enum": list(STATUSES)}},
                         "additionalProperties": False},
        "access": "read",
    },
    "list_pending_expenses": {
        "description": "Lista as despesas pendentes de aprovação (somente gestores), como aparecem no app.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "access": "read",
        "approvers_only": True,
    },
}


def visible_tools(user):
    return {name: tool for name, tool in MCP_TOOLS.items() if not tool.get("approvers_only") or is_approver(user["groups"])}


@app.get("/mcp/tools")
def tools(user=Depends(actor)):
    require_read(user)
    return {"tools": [{"name": name, "description": tool["description"], "input_schema": tool["input_schema"],
                       "access": tool["access"]} for name, tool in visible_tools(user).items()]}


@app.post("/mcp/tools/{tool}/execute")
def execute(tool: str, body: dict, user=Depends(actor), session=Depends(db.get_session)):
    if tool not in MCP_TOOLS:
        raise HTTPException(404, detail="Tool not found")
    require_read(user)
    if tool not in visible_tools(user):
        raise HTTPException(403, detail="Somente gestores veem as despesas pendentes")
    arguments = body.get("arguments", {})
    if not isinstance(arguments, dict):
        raise HTTPException(400, detail="arguments must be an object")
    if tool == "list_my_expenses":
        if set(arguments) - {"status"} or arguments.get("status", "pendente") not in STATUSES:
            raise HTTPException(400, detail="list_my_expenses takes only an optional status (pendente, aprovada, recusada)")
        expenses = db.list_my_expenses(session, user["email"], arguments.get("status"))
    else:
        if arguments != {}:
            raise HTTPException(400, detail="list_pending_expenses takes no arguments")
        expenses = db.list_pending(session)
    return {"result": {"expenses": [db.serialize(e) for e in expenses]}}


app.get("/theme.css")(theme)


@app.get("/{path:path}")
def spa(path: str):
    if path.split("/")[0] in {"api", "mcp", "health", "theme.css"}:
        raise HTTPException(404, detail="Not found")
    candidate = (STATIC / path).resolve()
    if candidate.is_relative_to(STATIC) and candidate.is_file():
        return FileResponse(candidate)
    # Missing assets must not return HTML to the JS/CSS loader.
    if path.startswith("assets/") or "." in Path(path).name:
        raise HTTPException(404, detail="Not found")
    if (STATIC / "index.html").is_file():
        return FileResponse(STATIC / "index.html")
    raise HTTPException(404, detail="Build the frontend or use the Vite dev server")
