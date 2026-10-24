from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings

_engine = None
_factory = None


class Base(DeclarativeBase):
    pass


def engine():
    global _engine, _factory
    if _engine is None:
        url = get_settings().database_url
        kw = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {"pool_pre_ping": True}
        _engine = create_engine(url, **kw)
        _factory = sessionmaker(_engine, expire_on_commit=False)
    return _engine


def reset_engine() -> None:
    """Drop the cached engine (tests switch databases between runs)."""
    global _engine, _factory
    if _engine is not None:
        _engine.dispose()
    _engine = _factory = None


def new_session():
    engine()
    return _factory()


@contextmanager
def session_scope():
    db = new_session()
    try:
        yield db
    finally:
        db.close()


def get_db():
    with session_scope() as db:
        yield db
