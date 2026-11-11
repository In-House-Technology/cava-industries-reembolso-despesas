# Reembolso de despesas (reembolso-despesas)

Quem pagou uma despesa registra data, descrição, categoria, valor e observação; o gestor aprova ou recusa (recusa exige motivo); cada pessoa acompanha o status das suas. Esta é a origem pública MIT de `cava-industries/reembolso-despesas`. A cópia instalada e os registros de despesas ficam na instância gerenciada da empresa.

## Instalar e usar na empresa

No Claude com o conector da InHouse ativo, peça: “Prepare a instalação de `cava-industries/reembolso-despesas`. Mostre quem pode ver e aprovar as despesas antes de eu decidir.” Revise as permissões, aprove a instalação e abra o link com seu login da empresa. Veja o [guia do catálogo](https://inhouse.technology/cloud/docs/catalog).

1. Registre uma despesa de exemplo e confira se ela aparece como pendente na sua lista.
2. Peça a outra pessoa, com acesso de aprovação, para decidir. O solicitante não pode decidir a própria despesa; uma recusa exige motivo.
3. Confira o status com a conta do solicitante. No Claude, as ferramentas deste app só consultam despesas; decisões e cancelamentos são feitos no app.

A versão 1.0.0 não oferece campos de configuração na instalação pelo catálogo. Para mudar os grupos de aprovação, solicite uma [alteração no app instalado](https://inhouse.technology/cloud/docs/build-an-app) e revise tanto o acesso na interface quanto o acesso da ferramenta MCP de pendências.

## Rules

- Everyone signed in (`ihc-users`) registers expenses and sees only their own.
- Approvers are members of the company sign-in groups in `APPROVER_GROUPS` (`|` separated; default `ihc-admins|reembolso-gestores`). They see every pending expense and approve or decline it. Nobody decides their own expense.
- Amounts are integer centavos (BRL). Dates cannot be in the future. Categories: Alimentação, Transporte, Hospedagem, Combustível, Material, Outros (`backend/app/models.py`).
- A pending expense can be cancelled by whoever registered it (soft delete). Decided expenses stay on record.
- MCP read tools: `list_my_expenses` (optional `status`) for everyone, `list_pending_expenses` for approvers only. Both use the same queries as the UI.

## Desenvolvimento local

Use Python 3.12 and Node 22. From the app root:

```sh
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
cp .env.example .env
# Set DATABASE_URL in .env to a development database you have permission to use.
# LOCAL_AUTH_MOCK_GROUPS=ihc-admins shows the approver view.
set -a; . ./.env; set +a
cd backend
../.venv/bin/alembic upgrade head
../.venv/bin/uvicorn app.main:app --reload --port 8080
```

In another terminal: `cd frontend && npm install && npm run dev`. Open the Vite URL; its proxy forwards relative `/api`, `/health` and `/theme.css` to the backend. Commit the generated package-lock.json for reproducible builds. Use `docker compose up --build` to run the built SPA instead; the database must be reachable from the container. Never add a database service to Compose.

Test: `cd backend && ../.venv/bin/python -m pytest -q`. Tests use SQLite through SQLAlchemy (in memory for ORM tests, a temporary database for the Alembic upgrade) and fake gateway JWKS; no Postgres is needed. The container runs `alembic upgrade head` before Uvicorn; when running Uvicorn locally, run the upgrade above first. `/health` returns `ok` and `COMMIT_SHA`/`GIT_SHA` (or `unknown`).

## Desenvolvimento e manutenção

Define typed SQLAlchemy `Mapped[]` models in `backend/app/models.py`, inheriting `BaseModel` alongside `Base`. Every table gets UUID `id` (default `uuid4`), UTC `created_at`/`modified_at` and their `*_epoch` seconds, plus nullable `deleted_at`/`deleted_at_epoch`. ORM insert/update events keep each timestamp and epoch pair current (including `decided_at`/`decided_at_epoch`); write through ORM instances so these events run. Soft-delete via `deleted_at`; the app session excludes deleted rows by default. Explicit administrative reads can use `execution_options(include_deleted=True)`.

Every schema change is a **new Alembic revision** in `backend/alembic/versions/`; never edit an applied revision. From `backend/` with the virtualenv active and `DATABASE_URL` set, run `alembic revision --autogenerate -m "describe the change"` against your development database (or write the revision by hand), review it, then `alembic upgrade head`. No URL or credentials belong in `alembic.ini`. The image's `/app` working directory contains the backend, so the declared `database.migrations_command: alembic upgrade head` runs there before serving.

Read the signed-in person from `X-authentik-email`, `-name`, `-username`, `-groups` (`|` separated). Trust these headers only behind the company proxy. Never add a login page; keep `auth.required: true`. `LOCAL_AUTH_MOCK_*` values are for your laptop only; provisioning strips `LOCAL_*` values.

`/mcp/tools` and `/mcp/tools/{tool}/execute` require a short RS256 actor JWT from the gateway: verify JWKS, issuer, audience, expiry and scope. Browser headers cannot replace it. Add tools in `backend/app/main.py` and `company-app.yaml`; writes must declare `access: write`, check the JWT's `confirmed_by`, and return `{result, audit: {previous, new, target}}`.

Use shadcn components in `frontend/src/components/ui` and theme variables only. `/theme.css` fetches `IHC_THEME_URL` server-side (2-second timeout, 60-second cache); unset/failing uses `backend/app/theme.css`. The theme link follows app CSS in `frontend/index.html`.

The platform provides one isolated database/user on shared Postgres (`DATABASE_URL`/`PG*`), sign-in, the MCP gateway and deployment. Never run your own datastore, mount a Docker socket or use host networking. Never commit `.env` or credentials. Keep tests and `/health`; keep `.gitea/workflows/deploy.yml` as provided. Changes to an installed app go through the managed check and approval flow; Git records the deployed code.

O check do InHouseCloud compila e roda este app numa área isolada antes de cada deploy pedido pelo chat.
