"""
Non-LLM validation for collected data fields.
Ensures data quality without relying on the language model.
"""
from __future__ import annotations

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class DataValidator:
    """
    Rule-based validation for collected parent data.
    This is the required non-LLM validation step.
    """

    # Valid weekdays
    VALID_WEEKDAYS = {
        "monday", "tuesday", "wednesday", "thursday",
        "friday", "saturday", "sunday"
    }

    # Email regex (RFC 5322 simplified)
    EMAIL_PATTERN = re.compile(
        r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    )

    # Phone pattern: allows various formats
    PHONE_PATTERN = re.compile(
        r'^[\+]?[1-9][\d\-\(\)\s]{6,14}[\d]$'
    )

    # Time pattern: HH:MM AM/PM or 24h format
    TIME_PATTERN = re.compile(
        r'^(?:1[0-2]|0?[1-9])(?::[0-5][0-9])?\s*(?:AM|PM|am|pm)$|^(?:[01]?[0-9]|2[0-3]):[0-5][0-9]$'
    )

    @classmethod
    def validate_all(cls, collected_fields: dict[str, str]) -> tuple[dict[str, str], list[str]]:
        """
        Validate all collected fields.

        Returns:
            (cleaned_fields, validation_flags)
            - cleaned_fields: validated and cleaned version of the data
            - validation_flags: list of warning/error messages
        """
        cleaned = {}
        flags = []

        for field_name, value in collected_fields.items():
            if not value or not str(value).strip():
                flags.append(f"{field_name}: empty value")
                continue

            value = str(value).strip()

            if field_name == "email":
                valid, clean_val, msg = cls.validate_email(value)
            elif field_name == "mobile_phone":
                valid, clean_val, msg = cls.validate_phone(value)
            elif field_name == "preferred_weekday":
                valid, clean_val, msg = cls.validate_weekday(value)
            elif field_name == "preferred_time":
                valid, clean_val, msg = cls.validate_time(value)
            elif field_name == "school_name":
                valid, clean_val, msg = cls.validate_school_name(value)
            elif field_name == "notes":
                valid, clean_val, msg = True, value, None
            else:
                valid, clean_val, msg = True, value, None

            if valid:
                cleaned[field_name] = clean_val
            else:
                flags.append(f"{field_name}: {msg}")
                # Still include the raw value but flag it
                cleaned[field_name] = value

        return cleaned, flags

    @classmethod
    def validate_email(cls, email: str) -> tuple[bool, str, Optional[str]]:
        """Validate email format."""
        email = email.lower().strip()
        if cls.EMAIL_PATTERN.match(email):
            return True, email, None
        return False, email, f"invalid email format: '{email}'"

    @classmethod
    def validate_phone(cls, phone: str) -> tuple[bool, str, Optional[str]]:
        """Validate phone number format."""
        # Clean common separators
        cleaned = re.sub(r'[\s\-\(\)]', '', phone)
        if len(cleaned) >= 7 and cleaned.lstrip('+').isdigit():
            return True, phone, None
        return False, phone, f"invalid phone format: '{phone}'"

    @classmethod
    def validate_weekday(cls, day: str) -> tuple[bool, str, Optional[str]]:
        """Validate weekday name."""
        day_lower = day.lower().strip()
        if day_lower in cls.VALID_WEEKDAYS:
            return True, day_lower.title(), None

        # Try partial match
        for valid_day in cls.VALID_WEEKDAYS:
            if valid_day.startswith(day_lower[:3]):
                return True, valid_day.title(), None

        return False, day, f"invalid weekday: '{day}'"

    @classmethod
    def validate_time(cls, time_str: str) -> tuple[bool, str, Optional[str]]:
        """Validate time format."""
        time_str = time_str.strip()
        if cls.TIME_PATTERN.match(time_str):
            return True, time_str, None

        # Try to parse common informal formats
        informal = time_str.lower()
        if "morning" in informal:
            return True, "9:00 AM", None
        if "afternoon" in informal:
            return True, "2:00 PM", None
        if "evening" in informal:
            return True, "5:00 PM", None

        return False, time_str, f"invalid time format: '{time_str}'"

    @classmethod
    def validate_school_name(cls, name: str) -> tuple[bool, str, Optional[str]]:
        """Validate school name (basic sanity check)."""
        name = name.strip()
        if len(name) < 3:
            return False, name, f"school name too short: '{name}'"
        if len(name) > 100:
            return False, name, f"school name too long: '{name}'"
        if any(c in name for c in ['@', '#', '$', '%']):
            return False, name, f"school name contains invalid characters: '{name}'"
        return True, name.title(), None

    @classmethod
    def calculate_confidence_adjustment(cls, flags: list[str]) -> float:
        """
        Calculate confidence adjustment based on validation flags.
        Returns a multiplier (0.0 to 1.0).
        """
        if not flags:
            return 1.0

        # Each flag reduces confidence
        reduction = len(flags) * 0.15
        return max(0.1, 1.0 - reduction)
