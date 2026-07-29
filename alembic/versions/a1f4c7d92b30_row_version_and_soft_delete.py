"""row version counter and patient soft delete

Closes two data-integrity gaps:

- `version` on patients and diagnoses gives sync an exact conflict signal.
  `updated_at` cannot serve that role: the tablet caches timestamps via drift,
  which stores DateTime as unix *seconds*, so same-second edits were
  indistinguishable and one write silently overwrote the other.

- `deleted_at` on patients replaces hard deletes. Clinical records must
  survive, and a hard DELETE of a patient holding diagnoses violated the
  foreign key — an unhandled 500.

The patient code uniqueness constraint becomes a partial unique index so a
code is reusable once the record is soft-deleted.

Revision ID: a1f4c7d92b30
Revises: 1342b192e0cd
Create Date: 2026-07-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a1f4c7d92b30'
down_revision: Union[str, None] = '1342b192e0cd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default seeds existing rows; the ORM supplies the value from here on.
    op.add_column(
        'patients',
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
    )
    op.add_column(
        'patients',
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'diagnoses',
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
    )

    # Live rows only: a soft-deleted patient must not reserve its code.
    op.drop_constraint('uq_patient_tenant_code', 'patients', type_='unique')
    op.create_index(
        'uq_patient_tenant_code',
        'patients',
        ['tenant_id', 'code'],
        unique=True,
        postgresql_where=sa.text('deleted_at IS NULL'),
        sqlite_where=sa.text('deleted_at IS NULL'),
    )

    # /sync/pull filters on updated_at; without these it is a sequential scan
    # that degrades as each hospital accumulates records.
    op.create_index('ix_patients_updated_at', 'patients', ['updated_at'])
    op.create_index('ix_diagnoses_updated_at', 'diagnoses', ['updated_at'])


def downgrade() -> None:
    op.drop_index('ix_diagnoses_updated_at', table_name='diagnoses')
    op.drop_index('ix_patients_updated_at', table_name='patients')

    # Going back means the code uniqueness rule applies to every row again,
    # including soft-deleted ones. If a code was reused after a deletion, that
    # constraint cannot be recreated — and the only ways forward are to destroy
    # patient records or to rename them. Neither is a migration's call to make,
    # so stop with an explanation instead of a bare UniqueViolation.
    duplicates = op.get_bind().execute(sa.text("""
        SELECT tenant_id, code, count(*) AS n
        FROM patients
        GROUP BY tenant_id, code
        HAVING count(*) > 1
    """)).fetchall()
    if duplicates:
        listed = ', '.join(f'{row.code} (x{row.n})' for row in duplicates)
        raise RuntimeError(
            "Cannot downgrade: these patient codes were reused after a soft "
            f"delete and are now duplicated across live and deleted rows: {listed}. "
            "Decide per record whether to purge the deleted row or re-code it, "
            "then run the downgrade again."
        )

    op.drop_index('uq_patient_tenant_code', table_name='patients')
    op.create_unique_constraint(
        'uq_patient_tenant_code', 'patients', ['tenant_id', 'code']
    )

    op.drop_column('diagnoses', 'version')
    op.drop_column('patients', 'deleted_at')
    op.drop_column('patients', 'version')
