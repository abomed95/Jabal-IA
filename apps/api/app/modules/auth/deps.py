import uuid
from dataclasses import dataclass

from fastapi import Depends, Header
from sqlalchemy import Connection, text

from app.core.db import get_conn
from app.core.errors import ApiError
from app.core.security import decode_access_token


@dataclass(frozen=True)
class CurrentUser:
    id: uuid.UUID
    role: str
    full_name: str
    phone: str
    level: str | None
    school: str | None
    device_id: uuid.UUID


def current_user(
    authorization: str | None = Header(default=None),
    conn: Connection = Depends(get_conn),
) -> CurrentUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ApiError(401, "missing_token", "Connexion requise.")
    claims = decode_access_token(authorization[7:].strip())
    row = conn.execute(
        text(
            """
            SELECT u.id, u.role, u.full_name, u.phone_e164, u.level, u.school
              FROM devices d JOIN users u ON u.id = d.user_id
             WHERE d.id = :did AND d.user_id = :uid AND d.revoked_at IS NULL
               AND u.is_active AND u.deleted_at IS NULL
            """
        ),
        {"did": claims["did"], "uid": claims["sub"]},
    ).first()
    if row is None:  # appareil révoqué (déconnexion, remplacement) : le jeton ne vaut plus rien
        raise ApiError(401, "session_revoked", "Cet appareil n'est plus connecté.")
    return CurrentUser(
        id=row.id,
        role=row.role,
        full_name=row.full_name,
        phone=row.phone_e164,
        level=row.level,
        school=row.school,
        device_id=uuid.UUID(claims["did"]),
    )


def require_role(*roles: str):
    def _dep(user: CurrentUser = Depends(current_user)) -> CurrentUser:
        if user.role not in roles:
            raise ApiError(403, "forbidden", "Action non autorisée pour ce compte.")
        return user

    return _dep
