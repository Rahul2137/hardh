"""
Google Sheets integration using gspread.
Handles reading/writing parent outreach data with idempotency.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import config
from models.parent import OutreachResult

logger = logging.getLogger(__name__)

# Sheet column headers for the Parent Outreach tab
PARENT_OUTREACH_HEADERS = OutreachResult.sheet_headers()

# Sheet name constants
SHEET_PARENT_OUTREACH = "Parent Outreach"
SHEET_WHATSAPP_LOG = "WhatsApp Log"
SHEET_AUDIT_LOG = "Audit Log"
SHEET_STUDENTS = "Students"

STUDENTS_HEADERS = [
    "student_id", "student_name", "grade", "parent_name", "parent_phone",
    "parent_email", "school_name", "preferred_weekday", "preferred_time",
    "notes", "status", "attempts", "last_contact"
]


class GoogleSheetsClient:
    """
    Real Google Sheets integration with simulation fallback.
    Uses gspread with a service account for authentication.
    """

    def __init__(self):
        self.client = None
        self.spreadsheet = None
        self.simulation_mode = config.SIMULATION_MODE
        self._local_data: dict[str, list[list[str]]] = {}  # Simulation storage

        if not self.simulation_mode:
            self._init_real_client()
        else:
            logger.info("Google Sheets running in SIMULATION mode")
            self._init_simulation()

    def _init_real_client(self):
        """Initialize real gspread client with service account."""
        try:
            import gspread
            from google.oauth2.service_account import Credentials

            creds_path = Path(config.GOOGLE_SHEETS_CREDENTIALS_FILE)
            if not creds_path.exists():
                logger.error(f"Credentials file not found: {creds_path}")
                logger.warning("Falling back to simulation mode")
                self.simulation_mode = True
                self._init_simulation()
                return

            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive",
            ]
            creds = Credentials.from_service_account_file(str(creds_path), scopes=scopes)
            self.client = gspread.authorize(creds)
            self.spreadsheet = self.client.open_by_key(config.GOOGLE_SHEET_ID)
            logger.info(f"Connected to Google Sheet: {self.spreadsheet.title}")

            # Ensure required worksheets exist
            self._ensure_worksheets()

        except Exception as e:
            logger.error(f"Google Sheets init failed: {e.__class__.__name__} - {str(e)}")
            logger.warning("Falling back to simulation mode")
            self.simulation_mode = True
            self._init_simulation()

    def _init_simulation(self):
        """Initialize local simulation data."""
        self._local_data = {
            SHEET_PARENT_OUTREACH: [PARENT_OUTREACH_HEADERS],
            SHEET_WHATSAPP_LOG: [
                ["Timestamp", "Parent ID", "Parent Name", "Phone", "Message", "Status"]
            ],
            SHEET_AUDIT_LOG: [
                ["Timestamp", "Action", "Parent ID", "Details", "Status"]
            ],
            SHEET_STUDENTS: [
                STUDENTS_HEADERS
            ]
        }

    def _ensure_worksheets(self):
        """Create worksheets if they don't exist."""
        if self.simulation_mode:
            return

        existing = [ws.title for ws in self.spreadsheet.worksheets()]

        for sheet_name, headers in [
            (SHEET_PARENT_OUTREACH, PARENT_OUTREACH_HEADERS),
            (SHEET_WHATSAPP_LOG, ["Timestamp", "Parent ID", "Parent Name", "Phone", "Message", "Status"]),
            (SHEET_AUDIT_LOG, ["Timestamp", "Action", "Parent ID", "Details", "Status"]),
            (SHEET_STUDENTS, STUDENTS_HEADERS),
        ]:
            if sheet_name not in existing:
                ws = self.spreadsheet.add_worksheet(title=sheet_name, rows=100, cols=20)
                ws.append_row(headers)
                logger.info(f"Created worksheet: {sheet_name}")
            else:
                # Check if headers exist
                ws = self.spreadsheet.worksheet(sheet_name)
                if not ws.row_values(1):
                    ws.append_row(headers)

    # ──────────────── Idempotent Write ────────────────

    def _row_exists(self, sheet_name: str, parent_id: str, channel: str, date_str: str) -> bool:
        """
        Check if a row with the same parent_id + channel + date already exists.
        This prevents duplicate entries when the same outreach is triggered twice.
        """
        if self.simulation_mode:
            rows = self._local_data.get(sheet_name, [])
            for row in rows[1:]:  # Skip header
                if len(row) >= 11 and row[0] == parent_id and row[10] == channel:
                    # Check if same date
                    if date_str in str(row[8]):
                        return True
            return False

        try:
            ws = self.spreadsheet.worksheet(sheet_name)
            all_values = ws.get_all_values()
            for row in all_values[1:]:
                if len(row) >= 11 and row[0] == parent_id and row[10] == channel:
                    if date_str in str(row[8]):
                        return True
            return False
        except Exception as e:
            logger.error(f"Error checking row existence: {e}")
            return False

    # ──────────────── Write Operations ────────────────

    def write_outreach_result(self, result: OutreachResult) -> bool:
        """
        Write an outreach result to the Parent Outreach sheet.
        Returns True if written, False if duplicate detected.
        """
        date_str = result.timestamp.strftime("%Y-%m-%d")

        # Idempotency check
        if self._row_exists(SHEET_PARENT_OUTREACH, result.parent_id, result.channel.value, date_str):
            logger.warning(
                f"Duplicate detected: {result.parent_id} / {result.channel.value} / {date_str}. Skipping."
            )
            return False

        row = result.to_sheet_row()

        if self.simulation_mode:
            self._local_data[SHEET_PARENT_OUTREACH].append(row)
            logger.info(f"[SIM] Wrote outreach result for {result.parent_id} to sheet")
            return True

        try:
            ws = self.spreadsheet.worksheet(SHEET_PARENT_OUTREACH)
            ws.append_row(row, value_input_option="USER_ENTERED")
            logger.info(f"Wrote outreach result for {result.parent_id} to Google Sheets")
            return True
        except Exception as e:
            logger.error(f"Failed to write outreach result: {e}")
            raise

    def log_whatsapp_message(
        self,
        parent_id: str,
        parent_name: str,
        phone: str,
        message: str,
        status: str = "sent",
    ) -> bool:
        """Log a WhatsApp message to the WhatsApp Log sheet."""
        date_str = datetime.now().strftime("%Y-%m-%d")

        # Idempotency: don't send same WhatsApp message twice on same day
        if self._row_exists(SHEET_WHATSAPP_LOG, parent_id, "whatsapp", date_str):
            logger.warning(f"WhatsApp already sent to {parent_id} today. Skipping duplicate.")
            return False

        row = [
            datetime.now().isoformat(),
            parent_id,
            parent_name,
            phone,
            message,
            status,
        ]

        if self.simulation_mode:
            self._local_data[SHEET_WHATSAPP_LOG].append(row)
            logger.info(f"[SIM] Logged WhatsApp message for {parent_id}")
            return True

        try:
            ws = self.spreadsheet.worksheet(SHEET_WHATSAPP_LOG)
            ws.append_row(row, value_input_option="USER_ENTERED")
            logger.info(f"Logged WhatsApp message for {parent_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to log WhatsApp message: {e}")
            raise

    def log_audit(self, action: str, parent_id: str, details: str, status: str):
        """Log an audit entry."""
        row = [datetime.now().isoformat(), action, parent_id, details, status]

        if self.simulation_mode:
            self._local_data[SHEET_AUDIT_LOG].append(row)
            return

        try:
            ws = self.spreadsheet.worksheet(SHEET_AUDIT_LOG)
            ws.append_row(row, value_input_option="USER_ENTERED")
        except Exception as e:
            logger.error(f"Failed to log audit entry: {e}")

    # ──────────────── Read Operations ────────────────

    def get_all_outreach_results(self) -> list[list[str]]:
        """Get all rows from the Parent Outreach sheet."""
        if self.simulation_mode:
            return self._local_data.get(SHEET_PARENT_OUTREACH, [])

        try:
            ws = self.spreadsheet.worksheet(SHEET_PARENT_OUTREACH)
            return ws.get_all_values()
        except Exception as e:
            logger.error(f"Failed to read outreach results: {e}")
            return []

    def get_all_students(self) -> list[dict]:
        """Get all students from the Students sheet."""
        if self.simulation_mode:
            rows = self._local_data.get(SHEET_STUDENTS, [])
            if not rows or len(rows) < 2:
                # If simulation is empty, try loading from local JSON
                try:
                    with open(config.DATA_DIR / "students.json", "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if data:
                            self.save_students(data)
                            return data
                except Exception:
                    pass
                return []
            
            headers = rows[0]
            result = []
            for row in rows[1:]:
                # Pad row to match headers
                padded_row = row + [""] * (len(headers) - len(row))
                item = dict(zip(headers, padded_row))
                # Cast integer
                try:
                    item["attempts"] = int(item.get("attempts", 0))
                except (ValueError, TypeError):
                    item["attempts"] = 0
                result.append(item)
            return result

        try:
            ws = self.spreadsheet.worksheet(SHEET_STUDENTS)
            records = ws.get_all_records()
            return records
        except Exception as e:
            logger.error(f"Failed to read students: {e}")
            return []

    def get_simulation_data(self) -> dict:
        """Return simulation data for inspection (testing/demo only)."""
        return self._local_data

    # ──────────────── Update Operations ────────────────

    def update_outreach_status(self, parent_id: str, new_status: str, notes: str = ""):
        """Update the status of an existing outreach row."""
        if self.simulation_mode:
            rows = self._local_data.get(SHEET_PARENT_OUTREACH, [])
            for row in rows[1:]:
                if row[0] == parent_id:
                    row[4] = new_status  # Status column
                    if notes:
                        row[12] = notes  # Notes column
                    logger.info(f"[SIM] Updated status for {parent_id} to {new_status}")
                    return
            return

        try:
            ws = self.spreadsheet.worksheet(SHEET_PARENT_OUTREACH)
            cell = ws.find(parent_id)
            if cell:
                ws.update_cell(cell.row, 5, new_status)
                if notes:
                    ws.update_cell(cell.row, 13, notes)
                logger.info(f"Updated status for {parent_id} to {new_status}")
        except Exception as e:
            logger.error(f"Failed to update status: {e}")

    def save_students(self, students: list[dict]):
        """Save a list of student dicts to the Students sheet."""
        if not students:
            return

        if self.simulation_mode:
            # Rebuild sheet
            rows = [STUDENTS_HEADERS]
            for s in students:
                row = [str(s.get(h, "")) for h in STUDENTS_HEADERS]
                rows.append(row)
            self._local_data[SHEET_STUDENTS] = rows
            logger.info("[SIM] Saved students to local data")
            
            # also save to JSON to persist during simulation
            with open(config.DATA_DIR / "students.json", "w", encoding="utf-8") as f:
                json.dump(students, f, indent=2, ensure_ascii=False)
            return

        try:
            ws = self.spreadsheet.worksheet(SHEET_STUDENTS)
            ws.clear()
            rows = [STUDENTS_HEADERS]
            for s in students:
                row = [str(s.get(h, "")) for h in STUDENTS_HEADERS]
                rows.append(row)
            ws.update(rows)
            logger.info(f"Saved {len(students)} students to Google Sheets")
        except Exception as e:
            logger.error(f"Failed to save students to sheets: {e}")
