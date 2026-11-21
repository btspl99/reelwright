"""Database engine, sessions and schema creation."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """Naive UTC timestamps everywhere (SQLite drops tzinfo, so we never store it)."""
    return datetime.now(UTC).replace(tzinfo=None)


_engines: dict[str, Engine] = {}
_sessionmakers: dict[str, sessionmaker] = {}


def get_engine() -> Engine:
    url = get_settings().database_url
    engine = _engines.get(url)
    if engine is None:
        kwargs: dict = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):

            @event.listens_for(engine, "connect")
            def _pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA busy_timeout=30000")
                cur.close()

        _engines[url] = engine
    return engine


def get_sessionmaker() -> sessionmaker:
    url = get_settings().database_url
    maker = _sessionmakers.get(url)
    if maker is None:
        maker = sessionmaker(bind=get_engine(), expire_on_commit=False)
        _sessionmakers[url] = maker
    return maker


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    from . import models  # noqa: F401  (register tables)

    get_settings().ensure_dirs()
    Base.metadata.create_all(get_engine())
