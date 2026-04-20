"""
Real Twilio WhatsApp integration.
Sends/receives real WhatsApp messages via Twilio's sandbox API.
Polls for incoming messages to maintain a conversation loop.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from twilio.rest import Client

from config import config

logger = logging.getLogger(__name__)


class TwilioWhatsAppClient:
    """
    Real Twilio WhatsApp client for sending and receiving messages.
    Uses polling to check for incoming replies (no webhook/ngrok needed).
    """

    def __init__(self):
        if not config.TWILIO_ACCOUNT_SID or not config.TWILIO_AUTH_TOKEN:
            raise ValueError(
                "TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be set in .env"
            )

        self.client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
        self.from_number = config.TWILIO_WHATSAPP_NUMBER
        self.sent_messages: list[dict] = []
        logger.info(f"Twilio WhatsApp client initialized (from: {self.from_number})")

    def send_message(self, to_number: str, body: str) -> dict:
        """
        Send a real WhatsApp message via Twilio.

        Args:
            to_number: WhatsApp number in format 'whatsapp:+1234567890'
            body: Message text

        Returns:
            dict with message_sid, status, timestamp
        """
        # Ensure whatsapp: prefix
        if not to_number.startswith("whatsapp:"):
            to_number = f"whatsapp:{to_number}"

        try:
            message = self.client.messages.create(
                body=body,
                from_=self.from_number,
                to=to_number,
            )

            result = {
                "message_sid": message.sid,
                "status": message.status,
                "timestamp": datetime.now().isoformat(),
                "to": to_number,
                "body": body,
                "direction": "outbound",
            }

            self.sent_messages.append(result)
            logger.info(f"[WhatsApp SENT] To: {to_number}")
            logger.info(f"  SID: {message.sid}")
            logger.info(f"  Status: {message.status}")
            logger.info(f"  Body: {body[:100]}...")

            return result

        except Exception as e:
            logger.error(f"[WhatsApp SEND FAILED] {e}")
            raise

    def get_latest_reply(
        self,
        from_number: str,
        after_timestamp: Optional[datetime] = None,
        timeout_seconds: int = 300,
        poll_interval: int = 5,
    ) -> Optional[dict]:
        """
        Poll for the latest incoming message from a specific number.
        Waits up to timeout_seconds, checking every poll_interval seconds.

        Args:
            from_number: The sender's WhatsApp number (e.g., 'whatsapp:+917999353493')
            after_timestamp: Only consider messages sent after this time
            timeout_seconds: How long to wait before giving up
            poll_interval: Seconds between polling attempts

        Returns:
            dict with body, timestamp, sid — or None if timeout
        """
        if not from_number.startswith("whatsapp:"):
            from_number = f"whatsapp:{from_number}"

        if after_timestamp is None:
            after_timestamp = datetime.now(timezone.utc) - timedelta(seconds=10)
        elif after_timestamp.tzinfo is None:
            after_timestamp = after_timestamp.replace(tzinfo=timezone.utc)

        start_time = time.time()
        attempt = 0

        logger.info(f"[WhatsApp] Waiting for reply from {from_number}...")
        logger.info(f"  Timeout: {timeout_seconds}s | Poll interval: {poll_interval}s")

        while (time.time() - start_time) < timeout_seconds:
            attempt += 1
            elapsed = int(time.time() - start_time)

            try:
                # Fetch recent messages from this number to our Twilio number
                messages = self.client.messages.list(
                    from_=from_number,
                    to=self.from_number,
                    date_sent_after=after_timestamp,
                    limit=5,
                )

                # Filter for messages we haven't processed yet
                for msg in messages:
                    msg_time = msg.date_sent or msg.date_created
                    if msg_time and msg_time.tzinfo is None:
                        msg_time = msg_time.replace(tzinfo=timezone.utc)

                    if msg_time and msg_time > after_timestamp:
                        reply = {
                            "body": msg.body,
                            "sid": msg.sid,
                            "timestamp": msg_time.isoformat(),
                            "from": from_number,
                            "status": msg.status,
                            "direction": "inbound",
                        }
                        logger.info(f"[WhatsApp RECEIVED] From: {from_number}")
                        logger.info(f"  Body: {msg.body}")
                        logger.info(f"  SID: {msg.sid}")
                        return reply

            except Exception as e:
                logger.warning(f"  Poll attempt {attempt} failed: {e}")

            # Show waiting indicator
            if attempt % 6 == 0:  # Every 30 seconds
                logger.info(f"  Still waiting... ({elapsed}s elapsed)")

            time.sleep(poll_interval)

        logger.warning(f"[WhatsApp] Timeout: no reply from {from_number} after {timeout_seconds}s")
        return None

    def get_sent_messages(self) -> list[dict]:
        """Return all sent messages for audit."""
        return self.sent_messages
