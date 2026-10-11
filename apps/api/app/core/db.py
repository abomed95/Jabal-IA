from collections.abc import Iterator

from sqlalchemy import Connection, create_engine
from sqlalchemy.engine import Engine

from app.core.config import get_settings

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings().database_url, pool_pre_ping=True, pool_size=5)
    return _engine


def get_conn() -> Iterator[Connection]:
    """Une transaction par requête : validée si tout va bien, annulée en cas d'exception."""
    with get_engine().begin() as conn:
        yield conn
