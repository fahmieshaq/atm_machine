"""empty message

Revision ID: 9e2d1afa9437
Revises: 5896343fd385
Create Date: 2022-08-26 13:59:31.497101

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9e2d1afa9437'
down_revision = '5896343fd385'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('my_pnl_config',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('symbol', sa.String(), nullable=True),
    sa.Column('loss_threshold', sa.Float(), nullable=True),
    sa.Column('profit_threshold', sa.Float(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('my_pnl_config')
    # ### end Alembic commands ###
