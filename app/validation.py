import re
from datetime import date

from app.exceptions import ValidationError

_MEMBER_NUMBER_RE = re.compile(r"^[A-Za-z0-9]{5,12}$")

ALLOWED_DIVISIONS = {
    "Open",
    "Limited",
    "Limited 10",
    "Production",
    "Single Stack",
    "Revolver",
    "Carry Optics",
    "PCC",
}


def validate_member_number(value: str) -> str:
    value = value.strip()
    if not _MEMBER_NUMBER_RE.match(value):
        raise ValidationError(
            "member_number",
            "must be alphanumeric and 5–12 characters",
        )
    return value


def validate_date_range(start: date, end: date) -> None:
    if start > end:
        raise ValidationError("date_range", "start date must be before end date")


def validate_division(division: str) -> str:
    if division not in ALLOWED_DIVISIONS:
        raise ValidationError(
            "division",
            f"must be one of: {', '.join(sorted(ALLOWED_DIVISIONS))}",
        )
    return division
