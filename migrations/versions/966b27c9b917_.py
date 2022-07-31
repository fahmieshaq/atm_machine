"""empty message

Revision ID: 966b27c9b917
Revises: 2befb900280f
Create Date: 2022-07-22 13:24:52.385681

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '966b27c9b917'
down_revision = '2befb900280f'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('tv_alert', sa.Column('my_risk_perc', sa.Float(), nullable=True))
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('tv_alert', 'my_risk_perc')
    # ### end Alembic commands ###
