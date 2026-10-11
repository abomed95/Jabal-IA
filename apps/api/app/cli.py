"""Outils d'exploitation : python -m app.cli create-admin --phone ... --name ..."""
import argparse
import getpass
import sys

from sqlalchemy import text

from app.core.db import get_engine
from app.core.security import hash_password, normalize_phone


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


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("create-admin")
    a.add_argument("--phone", required=True)
    a.add_argument("--name", required=True)
    args = parser.parse_args()
    if args.cmd == "create-admin":
        create_admin(args.phone, args.name, getpass.getpass("Mot de passe : "))
        print("Administrateur créé.")


if __name__ == "__main__":
    main()
