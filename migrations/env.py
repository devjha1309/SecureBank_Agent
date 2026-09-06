from alembic import context
from mock_bank.database.session import engine
from mock_bank.models.entities import Base

def run():
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
run()
