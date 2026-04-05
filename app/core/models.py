import json
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


# ─── Database Tables ──────────────────────────────


class AccessCode(SQLModel, table=True):
    __tablename__ = "access_code"

    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(unique=True, nullable=False, index=True)
    light_ids: str = Field(nullable=False)  # JSON array e.g. "[1, 2]"
    valid_from: datetime = Field(nullable=False)
    valid_until: datetime = Field(nullable=False)
    label: Optional[str] = None
    max_uses: Optional[int] = Field(default=None)  # None = unlimited, 1 = one-time code
    use_count: int = Field(default=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = Field(default=True)

    @property
    def light_ids_list(self) -> list[int]:
        return json.loads(self.light_ids)

    @light_ids_list.setter
    def light_ids_list(self, value: list[int]) -> None:
        self.light_ids = json.dumps(value)


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_log"

    id: Optional[int] = Field(default=None, primary_key=True)
    event: str = Field(nullable=False)  # DOOR_OPEN, LIGHT_ON, LIGHT_OFF, CODE_FAIL, REMOTE_DOOR, REMOTE_LIGHT
    code: Optional[str] = None
    light_ids: Optional[str] = None
    actor: str = Field(nullable=False)  # "keypad" | "api" | "button"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    details: Optional[str] = None


# ─── Request / Response Schemas ───────────────────


class AccessCodeCreate(SQLModel):
    code: str
    light_ids: list[int]
    valid_from: datetime
    valid_until: datetime
    label: Optional[str] = None
    max_uses: Optional[int] = None  # None = unlimited, 1 = one-time


class AccessCodeGenerate(SQLModel):
    light_ids: list[int]
    valid_from: datetime
    valid_until: datetime
    label: Optional[str] = None
    max_uses: Optional[int] = 1  # defaults to one-time
    code_length: int = 6


class AccessCodeUpdate(SQLModel):
    code: Optional[str] = None
    light_ids: Optional[list[int]] = None
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    label: Optional[str] = None
    max_uses: Optional[int] = None
    is_active: Optional[bool] = None


class AccessCodeRead(SQLModel):
    id: int
    code: str
    light_ids: list[int]
    valid_from: datetime
    valid_until: datetime
    label: Optional[str]
    max_uses: Optional[int]
    use_count: int
    is_active: bool
    created_at: datetime


class AuditLogRead(SQLModel):
    id: int
    event: str
    code: Optional[str]
    light_ids: Optional[str]
    actor: str
    timestamp: datetime
    details: Optional[str]
