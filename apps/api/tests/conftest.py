import os
import uuid

# La configuration doit être posée AVANT l'import de l'application.
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://postgres@localhost:5432/jabal_test")
os.environ.setdefault("JWT_SECRET", "test-secret-test-secret-test-secret-123")

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.db import get_engine  # noqa: E402
from app.core.throttle import login_throttle  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _database():
    """Repart d'un schéma vide et applique TOUTES les migrations : cela teste aussi les migrations."""
    with get_engine().begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "migrations"))
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def _clean():
    login_throttle.reset()
    yield
    with get_engine().begin() as conn:
        conn.execute(text("TRUNCATE users CASCADE"))  # vide aussi guardians, devices, abonnements, etc.


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    with get_engine().begin() as conn:
        yield conn


# ---- aides ----
PASSWORD = "motdepasse-solide"


def new_device() -> str:
    return str(uuid.uuid4())


def register(client, role="guardian", name="Parent Test", phone="77 00 00 01", level=None, password=PASSWORD):
    body = {"role": role, "full_name": name, "phone": phone, "password": password}
    if level:
        body["level"] = level
    return client.post("/v1/auth/register", json=body)


def login(client, phone="77 00 00 01", device_id=None, password=PASSWORD, **extra):
    body = {"phone": phone, "password": password, "device_id": device_id or new_device(), **extra}
    return client.post("/v1/auth/login", json=body)


def auth_header(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}
