"""
Pydantic models for parent/student records and outreach results.
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ────────────────────────────── Enums ──────────────────────────────

class OutreachStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    PARTIAL = "partial"
    CALLBACK_REQUESTED = "callback_requested"
    UNREACHABLE = "unreachable"
    WRONG_NUMBER = "wrong_number"
    OPT_OUT = "opt_out"
    HUMAN_REVIEW = "human_review"


class OutreachChannel(str, enum.Enum):
    VOICE = "voice"
    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"
    SMS = "sms"


class Weekday(str, enum.Enum):
    MONDAY = "Monday"
    TUESDAY = "Tuesday"
    WEDNESDAY = "Wednesday"
    THURSDAY = "Thursday"
    FRIDAY = "Friday"
    SATURDAY = "Saturday"
    SUNDAY = "Sunday"


# ────────────────────────────── Parent / Student ──────────────────

class ParentRecord(BaseModel):
    """A parent/student record. Null fields indicate missing data to collect."""

    parent_id: str
    parent_name: str
    student_name: str
    phone: str  # Always known — we need this to call them
    email: Optional[str] = None
    mobile_phone: Optional[str] = None
    school_name: Optional[str] = None
    preferred_weekday: Optional[str] = None
    preferred_time: Optional[str] = None
    notes: Optional[str] = None

    # Outreach metadata
    outreach_status: OutreachStatus = OutreachStatus.PENDING
    opt_out: bool = False
    last_contacted: Optional[datetime] = None
    attempt_count: int = 0

    def get_missing_fields(self) -> list[str]:
        """Return list of field names that are still missing/null."""
        check_fields = [
            "email", "mobile_phone", "school_name",
            "preferred_weekday", "preferred_time", "notes"
        ]
        return [f for f in check_fields if getattr(self, f) is None or getattr(self, f) == ""]

    def get_known_fields(self) -> dict:
        """Return dict of fields that already have values."""
        all_fields = [
            "email", "mobile_phone", "school_name",
            "preferred_weekday", "preferred_time", "notes"
        ]
        return {
            f: getattr(self, f)
            for f in all_fields
            if getattr(self, f) is not None and getattr(self, f) != ""
        }


# ────────────────────────────── Outreach Result ──────────────────

class CollectedField(BaseModel):
    """A single collected data field with confidence."""
    field_name: str
    value: str
    confidence: float = Field(ge=0.0, le=1.0)
    source: str = "voice_call"  # voice_call, whatsapp, manual


class OutreachResult(BaseModel):
    """Structured output from an outreach attempt — the core deliverable."""

    parent_id: str
    parent_name: str
    student_name: str
    collected_fields: dict[str, str] = Field(default_factory=dict)
    outreach_status: OutreachStatus
    confidence: float = Field(ge=0.0, le=1.0)
    last_action_taken: str
    next_recommended_action: str
    timestamp: datetime = Field(default_factory=datetime.now)
    attempt_number: int = 1
    channel: OutreachChannel = OutreachChannel.VOICE
    conversation_log: list[dict[str, str]] = Field(default_factory=list)
    validation_flags: list[str] = Field(default_factory=list)
    notes: str = ""

    def to_sheet_row(self) -> list[str]:
        """Convert to a flat row for Google Sheets."""
        collected_str = "; ".join(f"{k}={v}" for k, v in self.collected_fields.items())
        flags_str = "; ".join(self.validation_flags) if self.validation_flags else "none"
        return [
            self.parent_id,
            self.parent_name,
            self.student_name,
            collected_str,
            self.outreach_status.value,
            f"{self.confidence:.2f}",
            self.last_action_taken,
            self.next_recommended_action,
            self.timestamp.isoformat(),
            str(self.attempt_number),
            self.channel.value,
            flags_str,
            self.notes,
        ]

    @staticmethod
    def sheet_headers() -> list[str]:
        """Headers for the Google Sheet."""
        return [
            "Parent ID",
            "Parent Name",
            "Student Name",
            "Collected Fields",
            "Outreach Status",
            "Confidence",
            "Last Action Taken",
            "Next Recommended Action",
            "Timestamp",
            "Attempt Number",
            "Channel",
            "Validation Flags",
            "Notes",
        ]


# ────────────────────────────── Outreach Attempt Log ──────────────

class OutreachAttempt(BaseModel):
    """Log record for a single outreach attempt."""

    attempt_id: str
    parent_id: str
    channel: OutreachChannel
    timestamp: datetime = Field(default_factory=datetime.now)
    status: OutreachStatus
    duration_seconds: Optional[float] = None
    conversation_log: list[dict[str, str]] = Field(default_factory=list)
    result: Optional[OutreachResult] = None
    error: Optional[str] = None
