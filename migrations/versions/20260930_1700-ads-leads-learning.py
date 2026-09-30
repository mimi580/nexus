"""landing pages, leads, advertising, message variants

Revision ID: 7a3e5c1d9b24
Revises: 2f6b9d3c8e10
Create Date: 2026-09-30 17:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '7a3e5c1d9b24'
down_revision = '2f6b9d3c8e10'
branch_labels = None
depends_on = None


def _ts():
    return [
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    ]


def _indexes(table, columns, unique=()):
    with op.batch_alter_table(table, schema=None) as batch_op:
        for column in columns:
            batch_op.create_index(batch_op.f(f'ix_{table}_{column}'), [column], unique=column in unique)


def upgrade() -> None:
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.add_column(sa.Column('variant', sa.JSON(), nullable=False, server_default=sa.text("'{}'")))

    op.create_table('landing_pages',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('slug', sa.String(length=120), nullable=False),
    sa.Column('product_category', sa.String(length=50), nullable=False),
    sa.Column('country', sa.String(length=80), nullable=False),
    sa.Column('language', sa.String(length=10), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('content', sa.JSON(), nullable=False),
    sa.Column('facts', sa.JSON(), nullable=False),
    *_ts(),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_landing_pages'))
    )
    _indexes('landing_pages', ['country', 'product_category', 'slug', 'status'], unique=('slug',))

    op.create_table('page_events',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('landing_page_id', sa.String(length=40), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('campaign_id', sa.String(length=40), nullable=True),
    sa.Column('variant_key', sa.String(length=80), nullable=True),
    sa.Column('platform', sa.String(length=20), nullable=True),
    sa.Column('visitor_hash', sa.String(length=64), nullable=True),
    *_ts(),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_page_events'))
    )
    _indexes('page_events', ['campaign_id', 'kind', 'landing_page_id', 'visitor_hash'])

    op.create_table('leads',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('landing_page_id', sa.String(length=40), nullable=True),
    sa.Column('campaign_id', sa.String(length=40), nullable=True),
    sa.Column('variant_key', sa.String(length=80), nullable=True),
    sa.Column('platform', sa.String(length=20), nullable=False),
    sa.Column('attribution', sa.JSON(), nullable=False),
    sa.Column('full_name', sa.String(length=200), nullable=False),
    sa.Column('organisation', sa.String(length=300), nullable=True),
    sa.Column('email', sa.String(length=200), nullable=False),
    sa.Column('phone', sa.String(length=60), nullable=True),
    sa.Column('country', sa.String(length=80), nullable=True),
    sa.Column('product_category', sa.String(length=50), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=True),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('consent', sa.Boolean(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('opportunity_id', sa.String(length=40), nullable=True),
    sa.Column('visitor_hash', sa.String(length=64), nullable=True),
    sa.Column('conversions_sent', sa.JSON(), nullable=False),
    *_ts(),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_leads'))
    )
    _indexes('leads', ['campaign_id', 'email', 'landing_page_id', 'opportunity_id', 'platform',
                       'product_category', 'status', 'visitor_hash'])

    op.create_table('ad_campaigns',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('platform', sa.String(length=20), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('product_category', sa.String(length=50), nullable=False),
    sa.Column('countries', sa.JSON(), nullable=False),
    sa.Column('language', sa.String(length=10), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('daily_budget_usd', sa.Float(), nullable=False),
    sa.Column('landing_page_id', sa.String(length=40), nullable=True),
    sa.Column('targeting', sa.JSON(), nullable=False),
    sa.Column('external_ids', sa.JSON(), nullable=False),
    sa.Column('plan', sa.JSON(), nullable=False),
    sa.Column('status_reason', sa.Text(), nullable=True),
    sa.Column('launched_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_optimized_at', sa.DateTime(timezone=True), nullable=True),
    *_ts(),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ad_campaigns'))
    )
    _indexes('ad_campaigns', ['platform', 'product_category', 'status'])

    op.create_table('ad_variants',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('campaign_id', sa.String(length=40), nullable=False),
    sa.Column('key', sa.String(length=80), nullable=False),
    sa.Column('angle', sa.String(length=60), nullable=True),
    sa.Column('content', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('external_id', sa.String(length=120), nullable=True),
    sa.Column('asset_id', sa.String(length=40), nullable=True),
    sa.Column('status_reason', sa.Text(), nullable=True),
    *_ts(),
    sa.ForeignKeyConstraint(['campaign_id'], ['ad_campaigns.id'], name=op.f('fk_ad_variants_campaign_id_ad_campaigns')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ad_variants'))
    )
    _indexes('ad_variants', ['angle', 'campaign_id', 'external_id', 'key', 'status'])

    op.create_table('ad_metrics',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('campaign_id', sa.String(length=40), nullable=False),
    sa.Column('variant_key', sa.String(length=80), nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('impressions', sa.Integer(), nullable=False),
    sa.Column('clicks', sa.Integer(), nullable=False),
    sa.Column('spend_usd', sa.Float(), nullable=False),
    sa.Column('spend_native', sa.Float(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('ledgered_usd', sa.Float(), nullable=False),
    *_ts(),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ad_metrics')),
    sa.UniqueConstraint('campaign_id', 'variant_key', 'day', name='uq_ad_metrics_campaign_variant_day')
    )
    _indexes('ad_metrics', ['campaign_id', 'day'])

    op.create_table('ad_assets',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('product_category', sa.String(length=50), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('filename', sa.String(length=200), nullable=False),
    sa.Column('content_type', sa.String(length=60), nullable=False),
    sa.Column('data', sa.LargeBinary(), nullable=False),
    sa.Column('caption', sa.String(length=300), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('external_refs', sa.JSON(), nullable=False),
    *_ts(),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ad_assets'))
    )
    _indexes('ad_assets', ['product_category'])


def downgrade() -> None:
    for table, columns in (
        ('ad_assets', ['product_category']),
        ('ad_metrics', ['campaign_id', 'day']),
        ('ad_variants', ['angle', 'campaign_id', 'external_id', 'key', 'status']),
        ('ad_campaigns', ['platform', 'product_category', 'status']),
        ('leads', ['campaign_id', 'email', 'landing_page_id', 'opportunity_id', 'platform',
                   'product_category', 'status', 'visitor_hash']),
        ('page_events', ['campaign_id', 'kind', 'landing_page_id', 'visitor_hash']),
        ('landing_pages', ['country', 'product_category', 'slug', 'status']),
    ):
        with op.batch_alter_table(table, schema=None) as batch_op:
            for column in columns:
                batch_op.drop_index(batch_op.f(f'ix_{table}_{column}'))
        op.drop_table(table)
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.drop_column('variant')
