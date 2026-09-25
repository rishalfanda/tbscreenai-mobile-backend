"""device events

The record behind revoking a device and rebinding its MAC. `access_logs`
already says that someone sent POST to a device, but it deliberately holds
identifiers only, and Task 16 asks for more than that: a rebind must leave the
old and the new address behind, and a revocation its reason. `request_id`
joins each row here to its access-log entry.

Append-only: the application has no update or delete path for these rows.

`tenant_id` repeats the device's hospital, the same way diagnoses repeat their
patient's. A device never moves between hospitals, so the copy cannot drift,
and an audit of one hospital filters one column instead of joining through
`devices`.

Revision ID: f90d88e701c3
Revises: 94924b759b64
Create Date: 2026-09-24 10:30:30.119300

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'f90d88e701c3'
down_revision: Union[str, None] = '94924b759b64'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('device_events',
    sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('device_id', sa.Uuid(), nullable=False),
    sa.Column('actor_user_id', sa.Uuid(), nullable=False),
    sa.Column('actor_role', sa.String(length=20), nullable=False),
    sa.Column('event', sa.String(length=20), nullable=False),
    sa.Column('reason', sa.String(length=500), nullable=False),
    sa.Column('old_mac', sa.String(length=17), nullable=True),
    sa.Column('new_mac', sa.String(length=17), nullable=True),
    sa.Column('request_id', sa.String(length=36), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['hospitals.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_device_events_device_id'), 'device_events', ['device_id'], unique=False)
    op.create_index(op.f('ix_device_events_tenant_id'), 'device_events', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_device_events_tenant_id'), table_name='device_events')
    op.drop_index(op.f('ix_device_events_device_id'), table_name='device_events')
    op.drop_table('device_events')
