from alembic import context
from sqlalchemy import create_engine

from app import models  # noqa: F401  (registers tables)
from app.config import get_settings
from app.db import Base

url = get_settings().database_url


def run_migrations_online():
    engine = create_engine(url)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
