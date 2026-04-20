"""
WhatsApp message simulator.
Simulates sending WhatsApp messages when parents don't answer calls.

In production, this would use:
- Twilio WhatsApp API
- Meta Business API (Cloud API)
- 360dialog
"""
from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class WhatsAppSimulator:
    """
    Simulates WhatsApp message sending.
    Logs all messages for audit trail.
    """

    def __init__(self):
        self.sent_messages: list[dict] = []
        logger.info("WhatsApp Simulator initialized (simulation mode)")

    async def send_message(
        self,
        phone: str,
        message: str,
        parent_id: str,
        parent_name: str,
    ) -> dict:
        """
        Simulate sending a WhatsApp message.

        Returns:
            {
                "success": bool,
                "message_id": str,
                "timestamp": str,
                "phone": str,
                "status": str
            }
        """
        import uuid

        message_id = f"wamid.{uuid.uuid4().hex[:20]}"

        result = {
            "success": True,
            "message_id": message_id,
            "timestamp": datetime.now().isoformat(),
            "phone": phone,
            "parent_id": parent_id,
            "parent_name": parent_name,
            "message": message,
            "status": "sent",
            "channel": "whatsapp",
        }

        self.sent_messages.append(result)

        logger.info(f"📱 WhatsApp sent to {parent_name} ({phone})")
        logger.info(f"   Message ID: {message_id}")
        logger.info(f"   Content: {message[:100]}...")

        return result

    def get_sent_messages(self) -> list[dict]:
        """Return all sent messages for inspection."""
        return self.sent_messages

    async def check_delivery_status(self, message_id: str) -> str:
        """Simulate checking delivery status."""
        # In production: poll Twilio/Meta API for delivery receipt
        return "delivered"
