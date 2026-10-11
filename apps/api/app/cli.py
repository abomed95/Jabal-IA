"""Outils d'exploitation : python -m app.cli create-admin --phone ... --name ..."""
import argparse
import getpass
import sys
from pathlib import Path

from sqlalchemy import text

from app.core.db import get_engine
from app.core.security import hash_password, normalize_phone
from app.modules.content.ingest import IngestError, ingest_dir


def create_admin(phone: str, name: str, password: str) -> None:
    if len(password) < 12:
        sys.exit("Le mot de passe administrateur doit faire au moins 12 caractères.")
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (role, full_name, phone_e164, password_hash) "
                "VALUES ('admin', :n, :p, :h)"
            ),
            {"n": name, "p": normalize_phone(phone), "h": hash_password(password)},
        )


def ingest(directory: str) -> None:
    path = Path(directory)
    if not path.is_dir():
        sys.exit(f"Dossier introuvable : {directory}")
    try:
        with get_engine().begin() as conn:  # tout ou rien : une erreur annule tous les chapitres
            results = ingest_dir(conn, path)
    except IngestError as e:
        sys.exit(f"Ingestion refusée : {e}")
    for r in results:
        print(f"{r['file']}: {r['lessons']} leçons, {r['chunks']} extraits (brouillon)")


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("create-admin")
    a.add_argument("--phone", required=True)
    a.add_argument("--name", required=True)
    i = sub.add_parser("ingest", help="charge content/source/*.md en brouillon")
    i.add_argument("--dir", default="../../content/source")
    args = parser.parse_args()
    if args.cmd == "ingest":
        ingest(args.dir)
    elif args.cmd == "create-admin":
        create_admin(args.phone, args.name, getpass.getpass("Mot de passe : "))
        print("Administrateur créé.")


if __name__ == "__main__":
    main()
