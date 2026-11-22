"""Shared input rules for school names and staff mobile numbers."""
from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator, BeforeValidator


def format_school_name(value: str) -> str:
    """Use one spelling style for new names and names saved before validation."""
    value = " ".join(value.split()).lower()
    return re.sub(r"(^|[\s\-/\(])([^\W\d_])", lambda m: m[1] + m[2].upper(), value)


def validate_school_name(value):
    if not isinstance(value, str):
        return value
    if not any(char.isalpha() for char in value):
        raise ValueError("School name must contain letters, not just numbers or symbols.")
    return format_school_name(value)


def _strip(value):
    return value.strip() if isinstance(value, str) else value


def _mobile_number(value: str) -> str:
    if re.fullmatch(r"09[0-9]{9}", value) is None:
        raise ValueError("Enter an 11-digit Philippine mobile number starting with 09.")
    return value


MobileNumber = Annotated[str, BeforeValidator(_strip), AfterValidator(_mobile_number)]
