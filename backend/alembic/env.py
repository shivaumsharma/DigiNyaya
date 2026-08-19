import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# Allow `from app... import ...` when alembic is invoked from backend/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.auth.db import Base  # noqa: E402
from app.auth import orm_models  # noqa: E402,F401  (registers tables on Base.metadata)
from app.db_url import resolve_db_url  # noqa: E402

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Same DIGINYAYA_DB env var app/db.py and app/auth/db.py both read, so
# migrations always target the one database the app actually uses --
# alembic.ini's sqlalchemy.url placeholder is never read. resolve_db_url
# passes a full URL (e.g. postgresql+psycopg://...) through unchanged and
# only wraps a bare value as a sqlite file path -- previously this file
# unconditionally did the sqlite wrap, which silently mangled a real
# Postgres URL into `sqlite:///postgresql://...` and broke migrations.
_db_path = os.getenv(
    "DIGINYAYA_DB",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "diginyaya.db"),
)
config.set_main_option("sqlalchemy.url", resolve_db_url(_db_path))

target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # SQLite can't ALTER TABLE directly; batch mode recreates the table.
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
