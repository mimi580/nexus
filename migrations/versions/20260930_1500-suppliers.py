"""supplier leads, RFQs, draft offers, LinkedIn links

Revision ID: 2f6b9d3c8e10
Revises: 8d41c07e2a55
Create Date: 2026-09-30 15:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '2f6b9d3c8e10'
down_revision = '8d41c07e2a55'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('companies', schema=None) as batch_op:
        batch_op.add_column(sa.Column('kind', sa.String(length=20), nullable=False, server_default='buyer'))
        batch_op.add_column(sa.Column('status', sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column('profile', sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        batch_op.add_column(sa.Column('score', sa.Float(), nullable=True))
        batch_op.create_index(batch_op.f('ix_companies_kind'), ['kind'], unique=False)
        batch_op.create_index(batch_op.f('ix_companies_status'), ['status'], unique=False)

    with op.batch_alter_table('contacts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('linkedin_url', sa.String(length=300), nullable=True))

    with op.batch_alter_table('supplier_offers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('status', sa.String(length=20), nullable=False, server_default='active'))
        batch_op.add_column(sa.Column('rfq_id', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('currency', sa.String(length=3), nullable=False, server_default='USD'))
        batch_op.create_index(batch_op.f('ix_supplier_offers_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_supplier_offers_rfq_id'), ['rfq_id'], unique=False)

    op.create_table('supplier_rfqs',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('dedupe_key', sa.String(length=64), nullable=False),
    sa.Column('company_id', sa.String(length=40), nullable=False),
    sa.Column('contact_id', sa.String(length=40), nullable=True),
    sa.Column('product_category', sa.String(length=50), nullable=False),
    sa.Column('items', sa.JSON(), nullable=False),
    sa.Column('destination', sa.String(length=120), nullable=True),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('followups_sent', sa.Integer(), nullable=False),
    sa.Column('last_sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('next_followup_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('first_message_id', sa.String(length=40), nullable=True),
    sa.Column('reply_message_id', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name=op.f('fk_supplier_rfqs_company_id_companies')),
    sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], name=op.f('fk_supplier_rfqs_contact_id_contacts')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_supplier_rfqs'))
    )
    with op.batch_alter_table('supplier_rfqs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_supplier_rfqs_company_id'), ['company_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_supplier_rfqs_dedupe_key'), ['dedupe_key'], unique=True)
        batch_op.create_index(batch_op.f('ix_supplier_rfqs_next_followup_at'), ['next_followup_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_supplier_rfqs_product_category'), ['product_category'], unique=False)
        batch_op.create_index(batch_op.f('ix_supplier_rfqs_status'), ['status'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('supplier_rfqs', schema=None) as batch_op:
        for column in ('status', 'product_category', 'next_followup_at', 'dedupe_key', 'company_id'):
            batch_op.drop_index(batch_op.f(f'ix_supplier_rfqs_{column}'))
    op.drop_table('supplier_rfqs')
    with op.batch_alter_table('supplier_offers', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_supplier_offers_rfq_id'))
        batch_op.drop_index(batch_op.f('ix_supplier_offers_status'))
        batch_op.drop_column('currency')
        batch_op.drop_column('rfq_id')
        batch_op.drop_column('status')
    with op.batch_alter_table('contacts', schema=None) as batch_op:
        batch_op.drop_column('linkedin_url')
    with op.batch_alter_table('companies', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_companies_status'))
        batch_op.drop_index(batch_op.f('ix_companies_kind'))
        batch_op.drop_column('score')
        batch_op.drop_column('profile')
        batch_op.drop_column('status')
        batch_op.drop_column('kind')
