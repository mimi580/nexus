"""licence register

Revision ID: 5c2e9a1f7b30
Revises: dbf1a6974ad6
Create Date: 2026-09-30 10:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '5c2e9a1f7b30'
down_revision = 'dbf1a6974ad6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('licenses',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('holder_name', sa.String(length=200), nullable=False),
    sa.Column('license_types', sa.JSON(), nullable=False),
    sa.Column('country', sa.String(length=80), nullable=False),
    sa.Column('regions', sa.JSON(), nullable=False),
    sa.Column('coverage_countries', sa.JSON(), nullable=False),
    sa.Column('issuing_authority', sa.String(length=200), nullable=False),
    sa.Column('license_number', sa.String(length=120), nullable=False),
    sa.Column('product_categories', sa.JSON(), nullable=False),
    sa.Column('scope_notes', sa.Text(), nullable=False),
    sa.Column('valid_from', sa.Date(), nullable=True),
    sa.Column('expires_on', sa.Date(), nullable=True),
    sa.Column('document_ref', sa.String(length=500), nullable=True),
    sa.Column('verification', sa.String(length=20), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_licenses')),
    sa.UniqueConstraint('country', 'license_number', name='uq_license_country_number')
    )
    with op.batch_alter_table('licenses', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_licenses_active'), ['active'], unique=False)
        batch_op.create_index(batch_op.f('ix_licenses_country'), ['country'], unique=False)
        batch_op.create_index(batch_op.f('ix_licenses_expires_on'), ['expires_on'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('licenses', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_licenses_expires_on'))
        batch_op.drop_index(batch_op.f('ix_licenses_country'))
        batch_op.drop_index(batch_op.f('ix_licenses_active'))
    op.drop_table('licenses')
