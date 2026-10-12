import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Level = Literal["premiere_s", "terminale_s"]


class RegisterIn(BaseModel):
    role: Literal["student", "guardian"]
    full_name: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=8, max_length=24)
    password: str = Field(min_length=8, max_length=128)
    level: Level | None = None
    school: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _level_rules(self) -> "RegisterIn":
        if self.role == "student" and self.level is None:
            raise ValueError("level est obligatoire pour un élève")
        if self.role == "guardian" and self.level is not None:
            raise ValueError("level ne s'applique qu'à un élève")
        return self


class ChildIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    level: Level
    password: str = Field(min_length=8, max_length=128)
    school: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, min_length=8, max_length=24)  # défaut : celui du parent


class LoginIn(BaseModel):
    phone: str = Field(min_length=8, max_length=24)
    password: str = Field(min_length=1, max_length=128)
    device_id: uuid.UUID                       # généré une fois par l'appareil, conservé localement
    device_label: str | None = Field(default=None, max_length=80)
    role: Literal["student", "guardian", "admin"] | None = None   # pour lever une ambiguïté
    full_name: str | None = Field(default=None, max_length=120)   # idem (parent et enfants au même numéro)


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=200)


class UserOut(BaseModel):
    id: uuid.UUID
    role: str
    full_name: str
    phone: str
    level: str | None = None
    school: str | None = None


class TokensOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class ChildOut(UserOut):
    has_access: bool


class MeOut(UserOut):
    has_access: bool | None = None            # élève : abonnement valide ; sinon None
    children: list[ChildOut] = []
    guardians: list[UserOut] = []


class DeviceOut(BaseModel):
    id: uuid.UUID
    label: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    revoked: bool
    current: bool
