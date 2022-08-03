"""empty message

Revision ID: 279e98bf5b0b
Revises: 0ae2a80a0031
Create Date: 2022-07-31 18:33:25.635098

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '279e98bf5b0b'
down_revision = '0ae2a80a0031'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('my_logs', sa.Column('created_at', sa.DateTime(), nullable=True))
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('my_logs', 'created_at')
    # ### end Alembic commands ###