import hashlib
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings
from app.core.errors import ApiError

_ph = PasswordHasher()  # argon2id, paramètres par défaut de la bibliothèque
_DUMMY_HASH = _ph.hash("jabal-dummy-password")  # pour égaliser le temps quand le compte n'existe pas
_E164 = re.compile(r"^\+[1-9][0-9]{7,14}$")


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def burn_password_check(password: str) -> None:
    """Fait le même travail qu'une vraie vérification (anti-énumération de comptes par le temps)."""
    verify_password(password, _DUMMY_HASH)


def normalize_phone(raw: str) -> str:
    """'77 69 48 12' -> '+25377694812' ; '0025377694812' et '+253 77 69 48 12' acceptés."""
    cleaned = re.sub(r"[\s().-]", "", raw)
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    elif re.fullmatch(r"\d{8}", cleaned):
        cleaned = "+253" + cleaned
    if not _E164.fullmatch(cleaned):
        raise ApiError(422, "invalid_phone", "Numéro de téléphone invalide.")
    return cleaned


def new_refresh_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(48)
    return token, hash_refresh_token(token)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_access_token(user_id: uuid.UUID, role: str, device_id: uuid.UUID) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "role": role,
        "did": str(device_id),
        "iat": now,
        "exp": now + timedelta(seconds=s.access_ttl_seconds),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(
            token, get_settings().jwt_secret, algorithms=["HS256"], options={"require": ["exp", "sub", "did"]}
        )
    except jwt.PyJWTError as exc:
        raise ApiError(401, "invalid_token", "Session invalide ou expirée.") from exc
