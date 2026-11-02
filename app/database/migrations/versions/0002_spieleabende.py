"""Spieleabende: mehrere Partien mit gemeinsamer Punktzahl

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-01
"""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('game_nights',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=True),
    sa.Column('target_score', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.CheckConstraint("status IN ('ACTIVE','FINISHED')", name=op.f('ck_game_nights_status_valid')),
    sa.CheckConstraint('target_score > 0', name=op.f('ck_game_nights_target_positive')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_game_nights'))
    )
    with op.batch_alter_table('game_nights', schema=None) as batch_op:
        batch_op.create_index('ix_game_nights_created_at', ['created_at'], unique=False)
        batch_op.create_index('uq_game_nights_single_active', ['status'], unique=True,
                              sqlite_where=sa.text("status = 'ACTIVE'"))

    with op.batch_alter_table('games', schema=None) as batch_op:
        batch_op.add_column(sa.Column('night_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('night_number', sa.Integer(), nullable=True))
        batch_op.create_index('ix_games_night_id', ['night_id'], unique=False)
        batch_op.create_foreign_key(batch_op.f('fk_games_night_id_game_nights'), 'game_nights', ['night_id'], ['id'],
                                    ondelete='CASCADE')


def downgrade() -> None:
    with op.batch_alter_table('games', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_games_night_id_game_nights'), type_='foreignkey')
        batch_op.drop_index('ix_games_night_id')
        batch_op.drop_column('night_number')
        batch_op.drop_column('night_id')

    with op.batch_alter_table('game_nights', schema=None) as batch_op:
        batch_op.drop_index('uq_game_nights_single_active')
        batch_op.drop_index('ix_game_nights_created_at')

    op.drop_table('game_nights')
