"""Create the expenses table with the company base columns."""
from alembic import op
import sqlalchemy as sa

revision = "0001_expenses"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "expenses",
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at_epoch", sa.BigInteger(), nullable=False),
        sa.Column("modified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("modified_at_epoch", sa.BigInteger(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at_epoch", sa.BigInteger(), nullable=True),
        sa.Column("requester_email", sa.String(320), nullable=False),
        sa.Column("requester_name", sa.String(200), nullable=False),
        sa.Column("expense_date", sa.Date(), nullable=False),
        sa.Column("description", sa.String(200), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("notes", sa.String(1000), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("decided_by_email", sa.String(320), nullable=True),
        sa.Column("decided_by_name", sa.String(200), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at_epoch", sa.BigInteger(), nullable=True),
        sa.Column("decision_note", sa.String(1000), nullable=True),
    )
    op.create_index("expenses_requester_created", "expenses", ["requester_email", "created_at"])
    op.create_index("expenses_status_created", "expenses", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("expenses_status_created", table_name="expenses")
    op.drop_index("expenses_requester_created", table_name="expenses")
    op.drop_table("expenses")
