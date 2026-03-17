"""fix join_requests unique

Revision ID: 3a12766b4118
Revises: dec18e3b4ea8
Create Date: 2026-02-18 14:41:05.787799

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3a12766b4118'
down_revision: Union[str, Sequence[str], None] = 'dec18e3b4ea8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


from alembic import op
import sqlalchemy as sa

def upgrade() -> None:
    op.drop_constraint("uq_joinreq_user_group_status", "join_requests", type_="unique")
    op.create_index(
        "uq_joinreq_pending_user_group",
        "join_requests",
        ["user_id", "group_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'")
    )

def downgrade() -> None:
    op.drop_index("uq_joinreq_pending_user_group", table_name="join_requests")
    op.create_unique_constraint(
        "uq_joinreq_user_group_status",
        "join_requests",
        ["user_id", "group_id", "status"],
    )

