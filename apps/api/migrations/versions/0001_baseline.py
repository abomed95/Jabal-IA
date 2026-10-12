"""Schéma de base (db/schema.sql, figé).

Revision ID: 0001
Revises:
"""
from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = Path(__file__).resolve().parents[4] / "db" / "schema.sql"


def upgrade() -> None:
    # Passe par le curseur brut : le script contient des $$ et des % qui ne doivent pas être interprétés.
    raw = op.get_bind().connection.dbapi_connection
    with raw.cursor() as cur:
        cur.execute(SCHEMA.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("Le schéma de base ne se défait pas ; restaurez une sauvegarde.")
