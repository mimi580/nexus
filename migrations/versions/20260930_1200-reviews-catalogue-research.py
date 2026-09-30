"""review queue, supplier catalogue, price book, research sources, notifications

Revision ID: 8d41c07e2a55
Revises: 5c2e9a1f7b30
Create Date: 2026-09-30 12:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '8d41c07e2a55'
down_revision = '5c2e9a1f7b30'
branch_labels = None
depends_on = None


def _timestamps():
    return [
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table('review_items',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('review_key', sa.String(length=120), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('event_type', sa.String(length=80), nullable=False),
    sa.Column('title', sa.String(length=400), nullable=False),
    sa.Column('reasons', sa.JSON(), nullable=False),
    sa.Column('rule_ids', sa.JSON(), nullable=False),
    sa.Column('opportunity_id', sa.String(length=40), nullable=True),
    sa.Column('objective_id', sa.String(length=40), nullable=True),
    sa.Column('task_id', sa.String(length=40), nullable=True),
    sa.Column('action_kind', sa.String(length=40), nullable=True),
    sa.Column('action_payload', sa.JSON(), nullable=False),
    sa.Column('occurrences', sa.Integer(), nullable=False),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decided_by', sa.String(length=80), nullable=True),
    sa.Column('decision_note', sa.Text(), nullable=True),
    sa.Column('notified_at', sa.DateTime(timezone=True), nullable=True),
    *_timestamps(),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_review_items'))
    )
    with op.batch_alter_table('review_items', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_review_items_kind'), ['kind'], unique=False)
        batch_op.create_index(batch_op.f('ix_review_items_opportunity_id'), ['opportunity_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_review_items_review_key'), ['review_key'], unique=True)
        batch_op.create_index(batch_op.f('ix_review_items_status'), ['status'], unique=False)

    op.create_table('source_documents',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('url', sa.String(length=1000), nullable=False),
    sa.Column('domain', sa.String(length=255), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('query', sa.String(length=500), nullable=True),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('http_status', sa.Integer(), nullable=True),
    sa.Column('retrieved_at', sa.DateTime(timezone=True), nullable=False),
    *_timestamps(),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_source_documents'))
    )
    with op.batch_alter_table('source_documents', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_source_documents_content_hash'), ['content_hash'], unique=False)
        batch_op.create_index(batch_op.f('ix_source_documents_domain'), ['domain'], unique=False)
        batch_op.create_index(batch_op.f('ix_source_documents_url'), ['url'], unique=False)

    op.create_table('notifications',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('channel', sa.String(length=30), nullable=False),
    sa.Column('subject', sa.String(length=300), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('dedupe_key', sa.String(length=120), nullable=True),
    *_timestamps(),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notifications'))
    )
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_notifications_channel'), ['channel'], unique=False)
        batch_op.create_index(batch_op.f('ix_notifications_dedupe_key'), ['dedupe_key'], unique=False)
        batch_op.create_index(batch_op.f('ix_notifications_status'), ['status'], unique=False)

    op.create_table('supplier_offers',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('supplier_id', sa.String(length=40), nullable=False),
    sa.Column('product_category', sa.String(length=50), nullable=False),
    sa.Column('product_name', sa.String(length=300), nullable=False),
    sa.Column('condition', sa.String(length=80), nullable=True),
    sa.Column('quantity_available', sa.Integer(), nullable=True),
    sa.Column('moq', sa.Integer(), nullable=True),
    sa.Column('unit_cost_low_usd', sa.Float(), nullable=False),
    sa.Column('unit_cost_high_usd', sa.Float(), nullable=False),
    sa.Column('incoterm', sa.String(length=60), nullable=True),
    sa.Column('shipping_cost_usd', sa.Float(), nullable=True),
    sa.Column('lead_time_days', sa.Integer(), nullable=True),
    sa.Column('payment_terms', sa.String(length=200), nullable=True),
    sa.Column('documents', sa.JSON(), nullable=False),
    sa.Column('warranty', sa.String(length=200), nullable=True),
    sa.Column('valid_until', sa.Date(), nullable=True),
    sa.Column('source', sa.String(length=500), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    *_timestamps(),
    sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id'], name=op.f('fk_supplier_offers_supplier_id_suppliers')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_supplier_offers'))
    )
    with op.batch_alter_table('supplier_offers', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_supplier_offers_active'), ['active'], unique=False)
        batch_op.create_index(batch_op.f('ix_supplier_offers_product_category'), ['product_category'], unique=False)
        batch_op.create_index(batch_op.f('ix_supplier_offers_supplier_id'), ['supplier_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_supplier_offers_valid_until'), ['valid_until'], unique=False)

    op.create_table('price_references',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('product_category', sa.String(length=50), nullable=False),
    sa.Column('product_name', sa.String(length=300), nullable=True),
    sa.Column('condition', sa.String(length=80), nullable=True),
    sa.Column('country', sa.String(length=80), nullable=True),
    sa.Column('unit_price_low_usd', sa.Float(), nullable=False),
    sa.Column('unit_price_high_usd', sa.Float(), nullable=False),
    sa.Column('basis', sa.String(length=300), nullable=False),
    sa.Column('source', sa.String(length=500), nullable=False),
    sa.Column('valid_until', sa.Date(), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    *_timestamps(),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_price_references'))
    )
    with op.batch_alter_table('price_references', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_price_references_active'), ['active'], unique=False)
        batch_op.create_index(batch_op.f('ix_price_references_country'), ['country'], unique=False)
        batch_op.create_index(batch_op.f('ix_price_references_product_category'), ['product_category'], unique=False)
        batch_op.create_index(batch_op.f('ix_price_references_valid_until'), ['valid_until'], unique=False)

    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.add_column(sa.Column('thread_ref', sa.String(length=300), nullable=True))
        batch_op.add_column(sa.Column('from_address', sa.String(length=200), nullable=True))
        batch_op.create_index(batch_op.f('ix_messages_thread_ref'), ['thread_ref'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_messages_thread_ref'))
        batch_op.drop_column('from_address')
        batch_op.drop_column('thread_ref')
    for table, indexes in (
        ('price_references', ['active', 'country', 'product_category', 'valid_until']),
        ('supplier_offers', ['active', 'product_category', 'supplier_id', 'valid_until']),
        ('notifications', ['channel', 'dedupe_key', 'status']),
        ('source_documents', ['content_hash', 'domain', 'url']),
        ('review_items', ['kind', 'opportunity_id', 'review_key', 'status']),
    ):
        with op.batch_alter_table(table, schema=None) as batch_op:
            for column in indexes:
                batch_op.drop_index(batch_op.f(f'ix_{table}_{column}'))
        op.drop_table(table)
