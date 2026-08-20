"""add resident subroles and owner tenant approval

Revision ID: 20260326_06
Revises: 20260325_05
Create Date: 2026-03-26
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260326_06"
down_revision: Union[str, Sequence[str], None] = "20260325_05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    column_names = {column["name"] for column in inspector.get_columns("users")}
    if "resident_subrole" not in column_names:
        op.add_column(
            "users", sa.Column("resident_subrole", sa.String(20), nullable=True)
        )
    if "approved_by_owner_id" not in column_names:
        op.add_column(
            "users", sa.Column("approved_by_owner_id", sa.Integer(), nullable=True)
        )

    inspector = sa.inspect(bind)
    foreign_key_names = {
        foreign_key["name"] for foreign_key in inspector.get_foreign_keys("users")
    }
    foreign_key_name = "fk_users_approved_by_owner_id_users"
    if foreign_key_name not in foreign_key_names:
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table("users") as batch_op:
                batch_op.create_foreign_key(
                    foreign_key_name,
                    "users",
                    ["approved_by_owner_id"],
                    ["id"],
                    ondelete="SET NULL",
                )
        else:
            op.create_foreign_key(
                foreign_key_name,
                "users",
                "users",
                ["approved_by_owner_id"],
                ["id"],
                ondelete="SET NULL",
            )
    op.execute(
        "UPDATE users SET resident_subrole = 'owner' "
        "WHERE role = 'resident' AND is_approved = true"
    )
    inspector = sa.inspect(bind)
    index_name = "uq_users_one_approved_tenant_per_apartment"
    index_names = {index["name"] for index in inspector.get_indexes("users")}
    if index_name not in index_names:
        op.create_index(
            index_name,
            "users",
            ["apartment"],
            unique=True,
            postgresql_where=sa.text(
                "resident_subrole = 'tenant' AND is_approved = true"
            ),
            sqlite_where=sa.text(
                "resident_subrole = 'tenant' AND is_approved = 1"
            ),
        )


def downgrade() -> None:
    op.drop_index("uq_users_one_approved_tenant_per_apartment", table_name="users")
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("users") as batch_op:
            batch_op.drop_constraint(
                "fk_users_approved_by_owner_id_users", type_="foreignkey"
            )
    else:
        op.drop_constraint(
            "fk_users_approved_by_owner_id_users", "users", type_="foreignkey"
        )
    op.drop_column("users", "approved_by_owner_id")
    op.drop_column("users", "resident_subrole")
