"""access audit table

`sync_logs` records what a device pushed, not who opened a patient record on
screen. Medical-record regulation asks for the second, and this table is where
it comes from.

Append-only: the application has no update or delete path for these rows,
because a trail that can be edited is not a trail. Refused requests are
recorded too — a 403 against another hospital's patient is more interesting
than a 200 against your own.

`tenant_id` is nullable here, unlike every other table. A failed login has no
tenant yet, and refusing to record it would blind the log to exactly the events
worth watching.

Chained onto the device registry rather than onto row-versioning: both landed
independently from the same parent, and leaving them as two heads would make
`alembic upgrade head` refuse to run at all.

No request bodies, no response bodies, no query strings. An audit log that
copies the record it audits becomes a second store of the same protected data,
doubling both the surface to defend and the retention rules that apply.

Revision ID: 94924b759b64
Revises: 0420c9949e62
Create Date: 2026-09-10 14:31:07.122021

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '94924b759b64'
down_revision: Union[str, None] = '0420c9949e62'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('access_logs',
    sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=True),
    sa.Column('actor_user_id', sa.Uuid(), nullable=True),
    sa.Column('actor_role', sa.String(length=20), nullable=True),
    sa.Column('action', sa.String(length=10), nullable=False),
    sa.Column('resource_type', sa.String(length=20), nullable=True),
    sa.Column('resource_id', sa.Uuid(), nullable=True),
    sa.Column('method', sa.String(length=10), nullable=False),
    sa.Column('path', sa.String(length=255), nullable=False),
    sa.Column('status_code', sa.Integer(), nullable=False),
    sa.Column('client_ip', sa.String(length=45), nullable=True),
    sa.Column('request_id', sa.String(length=36), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['hospitals.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_access_logs_actor_user_id'), 'access_logs', ['actor_user_id'], unique=False)
    op.create_index(op.f('ix_access_logs_occurred_at'), 'access_logs', ['occurred_at'], unique=False)
    op.create_index(op.f('ix_access_logs_request_id'), 'access_logs', ['request_id'], unique=False)
    op.create_index(op.f('ix_access_logs_resource_id'), 'access_logs', ['resource_id'], unique=False)
    op.create_index(op.f('ix_access_logs_resource_type'), 'access_logs', ['resource_type'], unique=False)
    op.create_index(op.f('ix_access_logs_tenant_id'), 'access_logs', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_access_logs_tenant_id'), table_name='access_logs')
    op.drop_index(op.f('ix_access_logs_resource_type'), table_name='access_logs')
    op.drop_index(op.f('ix_access_logs_resource_id'), table_name='access_logs')
    op.drop_index(op.f('ix_access_logs_request_id'), table_name='access_logs')
    op.drop_index(op.f('ix_access_logs_occurred_at'), table_name='access_logs')
    op.drop_index(op.f('ix_access_logs_actor_user_id'), table_name='access_logs')
    op.drop_table('access_logs')
