"""A principal confirms identity; only the new inbox can complete recovery."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class EmailCorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_email: EmailStr = Field(max_length=255)
    current_password: str = Field(min_length=1, max_length=256, repr=False)
    reason: str = Field(min_length=1, max_length=500)
    identity_confirmed: Literal[True]

    @field_validator("reason", mode="before")
    @classmethod
    def trim_reason(cls, value):
        return value.strip() if isinstance(value, str) else value


class EmailCorrectionVerify(BaseModel):
    model_config = ConfigDict(extra="forbid")

    challenge_id: UUID
    new_email: EmailStr = Field(max_length=255)
    code: str = Field(pattern=r"^[0-9]{6}$", repr=False)

