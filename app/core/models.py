import json
from datetime import datetime, timezone
from typing import Optional

from pydantic import field_validator
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


class _ValidatedCodeMixin:
    @field_validator("light_ids")
    @classmethod
    def validate_light_ids(cls, v: list[int]) -> list[int]:
        if len(v) > 10:
            raise ValueError("light_ids must have 10 or fewer entries")
        for lid in v:
            if lid < 1 or lid > 10:
                raise ValueError(f"light_id must be between 1 and 10, got {lid}")
        return v

    @field_validator("label")
    @classmethod
    def validate_label(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v) > 100:
            raise ValueError("label must be 100 characters or fewer")
        return v


class AccessCodeCreate(_ValidatedCodeMixin, SQLModel):
    code: str = Field(min_length=4, max_length=16, pattern=r"^[0-9A-D]+$")
    light_ids: list[int]
    valid_from: datetime
    valid_until: datetime
    label: Optional[str] = None
    max_uses: Optional[int] = Field(default=None, ge=1)  # None = unlimited, 1 = one-time

    @field_validator("valid_until")
    @classmethod
    def validate_dates(cls, v: datetime, info) -> datetime:
        if "valid_from" in info.data and v <= info.data["valid_from"]:
            raise ValueError("valid_until must be after valid_from")
        return v


class AccessCodeGenerate(_ValidatedCodeMixin, SQLModel):
    light_ids: list[int]
    valid_from: datetime
    valid_until: datetime
    label: Optional[str] = None
    max_uses: Optional[int] = Field(default=1, ge=1)  # defaults to one-time
    code_length: int = Field(default=6, ge=4, le=10)

    @field_validator("valid_until")
    @classmethod
    def validate_dates(cls, v: datetime, info) -> datetime:
        if "valid_from" in info.data and v <= info.data["valid_from"]:
            raise ValueError("valid_until must be after valid_from")
        return v


class AccessCodeUpdate(SQLModel):
    code: Optional[str] = Field(default=None, min_length=4, max_length=16, pattern=r"^[0-9A-D]+$")
    light_ids: Optional[list[int]] = None
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    label: Optional[str] = Field(default=None, max_length=100)
    max_uses: Optional[int] = Field(default=None, ge=1)
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


class AccessCodeStatus(SQLModel):
    code: str
    status: str  # "active", "expired", "used", "inactive", "not_yet_valid", "not_found"
    label: Optional[str] = None
    light_ids: list[int] = []
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    max_uses: Optional[int] = None
    use_count: int = 0
    uses_remaining: Optional[int] = None  # None = unlimited


class AuditLogRead(SQLModel):
    id: int
    event: str
    code: Optional[str]
    light_ids: Optional[str]
    actor: str
    timestamp: datetime
    details: Optional[str]
