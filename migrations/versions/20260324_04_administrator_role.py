"""Add the administrator user role.

Revision ID: 20260324_04
Revises: 20260323_03
"""

from alembic import op
import sqlalchemy as sa


revision = "20260324_04"
down_revision = "20260323_03"
branch_labels = None
depends_on = None


def _sqlite_users_without_role_check(bind: sa.Connection) -> sa.Table:
    """Reflect the users table and remove only its role constraint."""
    metadata = sa.MetaData()
    users = sa.Table("users", metadata, autoload_with=bind)
    for constraint in list(users.constraints):
        if not isinstance(constraint, sa.CheckConstraint):
            continue
        expression = str(constraint.sqltext).lower()
        if "role" in expression and "dispatcher" in expression:
            users.constraints.remove(constraint)
    return users


def _replace_role_constraint(expression: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        current_checks = sa.inspect(bind).get_check_constraints("users")
        if any(
            str(check.get("sqltext", "")) == expression
            for check in current_checks
        ):
            return
        with op.batch_alter_table(
            "users",
            recreate="always",
            copy_from=_sqlite_users_without_role_check(bind),
        ) as batch_op:
            batch_op.create_check_constraint("ck_users_role", expression)
        return

    if bind.dialect.name == "postgresql":
        # The baseline left this check unnamed, so existing PostgreSQL schemas
        # may have an automatically generated constraint name.
        op.execute(
            sa.text(
                """
                DO $$
                DECLARE role_constraint text;
                BEGIN
                    FOR role_constraint IN
                        SELECT con.conname
                        FROM pg_constraint AS con
                        JOIN pg_class AS rel ON rel.oid = con.conrelid
                        WHERE rel.relname = 'users'
                          AND con.contype = 'c'
                          AND pg_get_constraintdef(con.oid)
                              LIKE '%role%resident%dispatcher%'
                    LOOP
                        EXECUTE format(
                            'ALTER TABLE users DROP CONSTRAINT %I',
                            role_constraint
                        );
                    END LOOP;
                END $$
                """
            )
        )
    else:
        op.drop_constraint("ck_users_role", "users", type_="check")

    op.create_check_constraint("ck_users_role", "users", expression)


def upgrade() -> None:
    _replace_role_constraint(
        "role IN ('resident', 'worker', 'dispatcher', 'administrator')"
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE users SET role = 'dispatcher' "
            "WHERE role = 'administrator'"
        )
    )
    _replace_role_constraint("role IN ('resident', 'worker', 'dispatcher')")
