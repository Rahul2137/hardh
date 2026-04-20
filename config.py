"""
Configuration management for the AI Outreach System.
Loads environment variables and provides centralized config access.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
PROJECT_ROOT = Path(__file__).parent
load_dotenv(PROJECT_ROOT / ".env")


class Config:
    """Centralized configuration."""

    # OpenAI
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = "gpt-4o"

    # Google Sheets
    GOOGLE_SHEETS_CREDENTIALS_FILE: str = os.getenv(
        "GOOGLE_SHEETS_CREDENTIALS_FILE", "credentials.json"
    )
    GOOGLE_SHEET_ID: str = os.getenv("GOOGLE_SHEET_ID", "")

    # Telegram
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

    # Twilio WhatsApp
    TWILIO_ACCOUNT_SID: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_WHATSAPP_NUMBER: str = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")
    YOUR_PHONE_NUMBER: str = os.getenv("YOUR_PHONE_NUMBER", "")

    # Simulation
    SIMULATION_MODE: bool = os.getenv("SIMULATION_MODE", "true").lower() == "true"

    # Quiet Hours (Part 3)
    QUIET_HOURS_START: int = 21  # 9 PM
    QUIET_HOURS_END: int = 8    # 8 AM

    # Confidence Thresholds
    HIGH_CONFIDENCE_THRESHOLD: float = 0.8
    LOW_CONFIDENCE_THRESHOLD: float = 0.5

    # Retry Config
    MAX_RETRIES: int = 3
    RETRY_BACKOFF_BASE: float = 2.0  # seconds

    # WhatsApp Conversation
    WHATSAPP_POLL_INTERVAL: int = 5   # seconds between polling for new messages
    WHATSAPP_TIMEOUT: int = 300       # seconds to wait for a reply before giving up

    # Paths
    DATA_DIR: Path = PROJECT_ROOT / "data"
    OUTPUTS_DIR: Path = PROJECT_ROOT / "outputs"
    DB_PATH: Path = PROJECT_ROOT / "audit.db"

    @classmethod
    def validate(cls):
        """Validate required configuration."""
        if not cls.SIMULATION_MODE:
            missing = []
            if not cls.OPENAI_API_KEY:
                missing.append("OPENAI_API_KEY")
            if not cls.GOOGLE_SHEET_ID:
                missing.append("GOOGLE_SHEET_ID")
            if missing:
                raise ValueError(
                    f"Missing required env vars for production mode: {', '.join(missing)}"
                )
        # Ensure directories exist
        cls.DATA_DIR.mkdir(exist_ok=True)
        cls.OUTPUTS_DIR.mkdir(exist_ok=True)


config = Config()
