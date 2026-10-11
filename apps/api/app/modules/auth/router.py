import uuid

from fastapi import APIRouter, Depends, Response
from sqlalchemy import Connection, text

from app.core.db import get_conn
from app.core.errors import ApiError
from app.modules.auth import service
from app.modules.auth.deps import CurrentUser, current_user, require_role
from app.modules.auth.schemas import (
    ChildIn,
    ChildOut,
    DeviceOut,
    LoginIn,
    MeOut,
    RefreshIn,
    RegisterIn,
    TokensOut,
    UserOut,
)

router = APIRouter(prefix="/v1", tags=["auth"])


@router.post("/auth/register", response_model=UserOut, status_code=201)
def register(data: RegisterIn, conn: Connection = Depends(get_conn)):
    return service.register(conn, data)


@router.post("/auth/login", response_model=TokensOut)
def login(data: LoginIn, conn: Connection = Depends(get_conn)):
    return service.login(conn, data)


@router.post("/auth/refresh", response_model=TokensOut)
def refresh(data: RefreshIn, conn: Connection = Depends(get_conn)):
    return service.refresh(conn, data.refresh_token)


@router.post("/auth/logout", status_code=204)
def logout(user: CurrentUser = Depends(current_user), conn: Connection = Depends(get_conn)):
    service.revoke_device(conn, user.id, user.device_id)
    return Response(status_code=204)


@router.get("/me", response_model=MeOut)
def me(user: CurrentUser = Depends(current_user), conn: Connection = Depends(get_conn)):
    out = MeOut(id=user.id, role=user.role, full_name=user.full_name, phone=user.phone,
                level=user.level, school=user.school)
    if user.role == "student":
        out.has_access = conn.execute(text("SELECT has_access(:s)"), {"s": user.id}).scalar_one()
        out.guardians = [
            UserOut(id=r.id, role=r.role, full_name=r.full_name, phone=r.phone_e164)
            for r in conn.execute(
                text("SELECT u.id, u.role, u.full_name, u.phone_e164 FROM guardians g "
                     "JOIN users u ON u.id = g.guardian_id WHERE g.student_id = :s"),
                {"s": user.id},
            )
        ]
    elif user.role == "guardian":
        out.children = [
            ChildOut(id=r.id, role=r.role, full_name=r.full_name, phone=r.phone_e164,
                     level=r.level, school=r.school, has_access=r.has_access)
            for r in conn.execute(
                text("SELECT u.id, u.role, u.full_name, u.phone_e164, u.level, u.school, "
                     "has_access(u.id) AS has_access FROM guardians g "
                     "JOIN users u ON u.id = g.student_id WHERE g.guardian_id = :g ORDER BY u.full_name"),
                {"g": user.id},
            )
        ]
    return out


@router.post("/guardians/children", response_model=UserOut, status_code=201)
def add_child(data: ChildIn, user: CurrentUser = Depends(require_role("guardian")),
              conn: Connection = Depends(get_conn)):
    return service.create_child(conn, user.id, user.phone, data)


@router.get("/devices", response_model=list[DeviceOut])
def list_devices(user: CurrentUser = Depends(current_user), conn: Connection = Depends(get_conn)):
    rows = conn.execute(
        text("SELECT id, label, first_seen_at, last_seen_at, revoked_at FROM devices "
             "WHERE user_id = :u ORDER BY first_seen_at"),
        {"u": user.id},
    )
    return [
        DeviceOut(id=r.id, label=r.label, first_seen_at=r.first_seen_at, last_seen_at=r.last_seen_at,
                  revoked=r.revoked_at is not None, current=r.id == user.device_id)
        for r in rows
    ]


@router.delete("/devices/{device_id}", status_code=204)
def delete_device(device_id: uuid.UUID, user: CurrentUser = Depends(current_user),
                  conn: Connection = Depends(get_conn)):
    if not service.revoke_device(conn, user.id, device_id):
        raise ApiError(404, "device_not_found", "Appareil introuvable.")
    return Response(status_code=204)
