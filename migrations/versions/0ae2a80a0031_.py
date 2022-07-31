"""empty message

Revision ID: 0ae2a80a0031
Revises: 3abf5ea48238
Create Date: 2022-07-31 16:03:59.407938

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0ae2a80a0031'
down_revision = '3abf5ea48238'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('my_logs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('my_logs')
    # ### end Alembic commands ###
