"""Ausgangsschema

Revision ID: 0001
Revises: 
Create Date: 2026-11-01
"""
from alembic import op
import sqlalchemy as sa


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('games',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=True),
    sa.Column('target_score', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('current_round', sa.Integer(), nullable=False),
    sa.Column('auto_finish', sa.Boolean(), nullable=False),
    sa.Column('finish_reason', sa.String(length=16), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=True),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.CheckConstraint("status IN ('CREATED','ACTIVE','FINISHED','CANCELLED')", name=op.f('ck_games_status_valid')),
    sa.CheckConstraint('target_score > 0', name=op.f('ck_games_target_positive')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_games'))
    )
    with op.batch_alter_table('games', schema=None) as batch_op:
        batch_op.create_index('ix_games_created_at', ['created_at'], unique=False)
        batch_op.create_index('ix_games_status', ['status'], unique=False)
        # V1: höchstens ein aktives Hauptspiel – zusätzlich zur Prüfung im Spieldienst auch in der Datenbank.
        batch_op.create_index('uq_games_single_active', ['status'], unique=True,
                              sqlite_where=sa.text("status = 'ACTIVE'"))

    op.create_table('idempotency_requests',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('scope', sa.String(length=80), nullable=False),
    sa.Column('status_code', sa.Integer(), nullable=False),
    sa.Column('response', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('key', name=op.f('pk_idempotency_requests'))
    )
    with op.batch_alter_table('idempotency_requests', schema=None) as batch_op:
        batch_op.create_index('ix_idempotency_requests_created_at', ['created_at'], unique=False)

    op.create_table('players',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('display_name', sa.String(length=40), nullable=False),
    sa.Column('name_key', sa.String(length=40), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_players')),
    sa.UniqueConstraint('name_key', name=op.f('uq_players_name_key'))
    )
    op.create_table('game_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('game_id', sa.Integer(), nullable=False),
    sa.Column('event_type', sa.String(length=40), nullable=False),
    sa.Column('payload', sa.Text(), nullable=False),
    sa.Column('actor', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['game_id'], ['games.id'], name=op.f('fk_game_events_game_id_games'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_game_events'))
    )
    with op.batch_alter_table('game_events', schema=None) as batch_op:
        batch_op.create_index('ix_game_events_game_id', ['game_id'], unique=False)

    op.create_table('game_players',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('game_id', sa.Integer(), nullable=False),
    sa.Column('player_id', sa.Integer(), nullable=False),
    sa.Column('display_order', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['game_id'], ['games.id'], name=op.f('fk_game_players_game_id_games'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['player_id'], ['players.id'], name=op.f('fk_game_players_player_id_players'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_game_players')),
    sa.UniqueConstraint('game_id', 'player_id', name=op.f('uq_game_players_game_id_player_id'))
    )
    with op.batch_alter_table('game_players', schema=None) as batch_op:
        batch_op.create_index('ix_game_players_player_id', ['player_id'], unique=False)

    op.create_table('rounds',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('game_id', sa.Integer(), nullable=False),
    sa.Column('round_number', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint('round_number > 0', name=op.f('ck_rounds_number_positive')),
    sa.ForeignKeyConstraint(['game_id'], ['games.id'], name=op.f('fk_rounds_game_id_games'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rounds')),
    sa.UniqueConstraint('game_id', 'round_number', name=op.f('uq_rounds_game_id_round_number'))
    )
    op.create_table('round_scores',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('round_id', sa.Integer(), nullable=False),
    sa.Column('player_id', sa.Integer(), nullable=False),
    sa.Column('points', sa.Integer(), nullable=False),
    sa.CheckConstraint('points >= 0', name=op.f('ck_round_scores_points_not_negative')),
    sa.ForeignKeyConstraint(['player_id'], ['players.id'], name=op.f('fk_round_scores_player_id_players'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['round_id'], ['rounds.id'], name=op.f('fk_round_scores_round_id_rounds'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_round_scores')),
    sa.UniqueConstraint('round_id', 'player_id', name=op.f('uq_round_scores_round_id_player_id'))
    )
    with op.batch_alter_table('round_scores', schema=None) as batch_op:
        batch_op.create_index('ix_round_scores_player_id', ['player_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('round_scores', schema=None) as batch_op:
        batch_op.drop_index('ix_round_scores_player_id')

    op.drop_table('round_scores')
    op.drop_table('rounds')
    with op.batch_alter_table('game_players', schema=None) as batch_op:
        batch_op.drop_index('ix_game_players_player_id')

    op.drop_table('game_players')
    with op.batch_alter_table('game_events', schema=None) as batch_op:
        batch_op.drop_index('ix_game_events_game_id')

    op.drop_table('game_events')
    op.drop_table('players')
    with op.batch_alter_table('idempotency_requests', schema=None) as batch_op:
        batch_op.drop_index('ix_idempotency_requests_created_at')

    op.drop_table('idempotency_requests')
    with op.batch_alter_table('games', schema=None) as batch_op:
        batch_op.drop_index('uq_games_single_active')
        batch_op.drop_index('ix_games_status')
        batch_op.drop_index('ix_games_created_at')

    op.drop_table('games')
