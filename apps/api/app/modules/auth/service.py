import uuid

from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.security import (
    burn_password_check,
    create_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    normalize_phone,
    verify_password,
)
from app.core.throttle import login_throttle
from app.modules.auth.schemas import ChildIn, LoginIn, RegisterIn, TokensOut, UserOut

_USER_COLS = "id, role, full_name, phone_e164, level, school"


def _user_out(row) -> UserOut:
    return UserOut(
        id=row.id, role=row.role, full_name=row.full_name, phone=row.phone_e164,
        level=row.level, school=row.school,
    )


def _insert_user(conn: Connection, *, role: str, full_name: str, phone: str, password: str,
                 level: str | None, school: str | None):
    try:
        with conn.begin_nested():  # point de sauvegarde : un doublon n'annule pas toute la requête
            return conn.execute(
                text(
                    f"""
                    INSERT INTO users (role, full_name, phone_e164, password_hash, level, school)
                    VALUES (:role, :full_name, :phone, :pw, :level, :school)
                    RETURNING {_USER_COLS}
                    """
                ),
                {"role": role, "full_name": full_name.strip(), "phone": phone,
                 "pw": hash_password(password), "level": level, "school": school},
            ).one()
    except IntegrityError as exc:
        raise ApiError(409, "account_exists", "Un compte existe déjà avec ce nom et ce numéro.") from exc


def register(conn: Connection, data: RegisterIn) -> UserOut:
    row = _insert_user(
        conn, role=data.role, full_name=data.full_name, phone=normalize_phone(data.phone),
        password=data.password, level=data.level, school=data.school,
    )
    return _user_out(row)


def create_child(conn: Connection, guardian_id: uuid.UUID, guardian_phone: str, data: ChildIn) -> UserOut:
    phone = normalize_phone(data.phone) if data.phone else guardian_phone
    row = _insert_user(
        conn, role="student", full_name=data.full_name, phone=phone, password=data.password,
        level=data.level, school=data.school,
    )
    # Le parent qui crée le compte de l'enfant donne son consentement à cet instant.
    conn.execute(
        text(
            "INSERT INTO guardians (student_id, guardian_id, relation, consent_at) "
            "VALUES (:s, :g, 'parent_ou_tuteur', now())"
        ),
        {"s": row.id, "g": guardian_id},
    )
    return _user_out(row)


def _find_account(conn: Connection, data: LoginIn, phone: str):
    sql = (
        f"SELECT {_USER_COLS}, password_hash FROM users "
        "WHERE phone_e164 = :phone AND is_active AND deleted_at IS NULL"
    )
    params: dict = {"phone": phone}
    if data.role:
        sql += " AND role = :role"
        params["role"] = data.role
    if data.full_name:
        sql += " AND lower(full_name) = lower(:full_name)"
        params["full_name"] = data.full_name.strip()
    candidates = conn.execute(text(sql), params).all()
    matches = [c for c in candidates if verify_password(data.password, c.password_hash)]
    if not candidates:
        burn_password_check(data.password)
    return matches


def login(conn: Connection, data: LoginIn) -> TokensOut:
    phone = normalize_phone(data.phone)
    if login_throttle.is_blocked(phone):
        raise ApiError(429, "too_many_attempts", "Trop de tentatives. Réessayez dans 15 minutes.")
    matches = _find_account(conn, data, phone)
    if not matches:
        login_throttle.record_failure(phone)
        raise ApiError(401, "invalid_credentials", "Numéro ou mot de passe incorrect.")
    if len(matches) > 1:
        raise ApiError(409, "ambiguous_account",
                       "Plusieurs comptes correspondent : précisez le rôle ou le nom complet.")
    user = matches[0]
    login_throttle.reset(phone)

    refresh, refresh_hash = new_refresh_token()
    device_id = _bind_device(conn, user.id, data, refresh_hash)
    return TokensOut(
        access_token=create_access_token(user.id, user.role, device_id),
        refresh_token=refresh,
        expires_in=get_settings().access_ttl_seconds,
        user=_user_out(user),
    )


def _bind_device(conn: Connection, user_id: uuid.UUID, data: LoginIn, refresh_hash: str) -> uuid.UUID:
    """Reconnaît l'appareil par son identifiant local ; un nouvel appareil consomme un emplacement (2 max)."""
    try:
        with conn.begin_nested():
            existing = conn.execute(
                text("SELECT id FROM devices WHERE user_id = :u AND client_device_id = :c FOR UPDATE"),
                {"u": user_id, "c": data.device_id},
            ).first()
            if existing:
                # Reconnexion : met à jour l'appareil, et le réactive s'il était révoqué (le déclencheur
                # vérifie alors la limite de 2).
                conn.execute(
                    text(
                        "UPDATE devices SET token_hash = :h, revoked_at = NULL, last_seen_at = now(), "
                        "label = coalesce(:l, label) WHERE id = :id"
                    ),
                    {"h": refresh_hash, "l": data.device_label, "id": existing.id},
                )
                return existing.id
            return conn.execute(
                text(
                    "INSERT INTO devices (user_id, client_device_id, label, token_hash) "
                    "VALUES (:u, :c, :l, :h) RETURNING id"
                ),
                {"u": user_id, "c": data.device_id, "l": data.device_label, "h": refresh_hash},
            ).scalar_one()
    except DBAPIError as exc:
        if "device_limit_reached" in str(exc.orig):
            raise ApiError(
                409, "device_limit_reached",
                "Ce compte est déjà connecté sur 2 appareils. Déconnectez-en un pour continuer.",
            ) from exc
        raise


def refresh(conn: Connection, token: str) -> TokensOut:
    ttl_days = get_settings().refresh_ttl_days
    row = conn.execute(
        text(
            """
            SELECT d.id AS device_id, u.id, u.role, u.full_name, u.phone_e164, u.level, u.school
              FROM devices d JOIN users u ON u.id = d.user_id
             WHERE d.token_hash = :h AND d.revoked_at IS NULL AND u.is_active AND u.deleted_at IS NULL
               AND d.last_seen_at > now() - make_interval(days => :ttl)
             FOR UPDATE OF d
            """
        ),
        {"h": hash_refresh_token(token), "ttl": ttl_days},
    ).first()
    if row is None:
        raise ApiError(401, "invalid_refresh", "Session expirée : reconnectez-vous.")
    new_token, new_hash = new_refresh_token()  # rotation : l'ancien jeton cesse de fonctionner
    conn.execute(
        text("UPDATE devices SET token_hash = :h, last_seen_at = now() WHERE id = :id"),
        {"h": new_hash, "id": row.device_id},
    )
    return TokensOut(
        access_token=create_access_token(row.id, row.role, row.device_id),
        refresh_token=new_token,
        expires_in=get_settings().access_ttl_seconds,
        user=_user_out(row),
    )


def revoke_device(conn: Connection, user_id: uuid.UUID, device_id: uuid.UUID) -> bool:
    res = conn.execute(
        text("UPDATE devices SET revoked_at = now() WHERE id = :d AND user_id = :u AND revoked_at IS NULL"),
        {"d": device_id, "u": user_id},
    )
    return res.rowcount == 1
