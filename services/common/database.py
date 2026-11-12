"""Database connection and session pooling manager."""

import os
from collections.abc import Generator
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


def get_db_pool_size() -> int:
    """Returns DB_POOL_SIZE from environment variables (default 10)."""
    return int(os.getenv("DB_POOL_SIZE", "10"))


def get_database_url() -> str:
    """Returns DATABASE_URL from environment variables."""
    return os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/scopex")


def create_db_engine(db_url: str | None = None, pool_size: int | None = None) -> Engine:
    """Creates a SQLAlchemy engine honoring configurable connection pool sizes."""
    url = db_url or get_database_url()
    size = pool_size if pool_size is not None else get_db_pool_size()
    max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "5"))

    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})

    # Default postgresql driver to psycopg2 if unspecified
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)

    return create_engine(
        url,
        pool_size=size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        pool_recycle=1800,
    )


def get_session_factory(engine: Engine) -> sessionmaker:
    """Returns a sessionmaker bound to the given engine."""
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db(session_factory: sessionmaker) -> Generator[Any, None, None]:
    """Dependency helper yielding an isolated database session."""
    db = session_factory()
    try:
        yield db
    finally:
        db.close()
