"""Only the public values needed for pre-registration email verification."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class RegistrationEmailRequest(BaseModel):
    email: EmailStr
    kind: Literal["teacher", "school"]
    captcha_token: str | None = Field(default=None, max_length=2048, repr=False)


class RegistrationEmailVerify(BaseModel):
    challenge_id: UUID
    email: EmailStr
    code: str = Field(pattern=r"^[0-9]{6}$", repr=False)
