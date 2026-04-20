"""
WhatsApp Conversation Agent — Real multi-turn WhatsApp conversations.

STRATEGY:
1. Ask parent ONE field at a time
2. If parent answers → extract the field, move to next
3. If answer is unclear → retry same question (max 2 retries per field)
4. After 2 failed retries → send "our agent will connect shortly" → human_review
5. If parent says opt-out/wrong number/callback → handle immediately
6. Once all fields collected → thank and end

Uses Twilio for real WhatsApp messaging and OpenAI for extraction.
Falls back to regex-based extraction if OpenAI is unavailable.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

from config import config
from models.parent import (
    ParentRecord,
    OutreachResult,
    OutreachStatus,
    OutreachChannel,
)
from integrations.twilio_whatsapp import TwilioWhatsAppClient
from integrations.google_sheets import GoogleSheetsClient
from reliability.validation import DataValidator
from reliability.audit import AuditLogger
from reliability.idempotency import IdempotencyManager

logger = logging.getLogger(__name__)


# ─────────────── Field question templates ───────────────

FIELD_QUESTIONS = {
    "email": "Could you please share your email address? 📧",
    "mobile_phone": "What's the best mobile number to reach you? 📱",
    "school_name": "Which school does {student} attend? 🏫",
    "preferred_weekday": "What day of the week works best for {student}'s group event? (e.g., Monday, Wednesday) 📅",
    "preferred_time": "And what time would you prefer? (e.g., 4:00 PM, 10 AM) ⏰",
    "notes": "Any special notes we should know about {student}? (allergies, preferences, anything!) If none, just say 'none'. 📝",
}

RETRY_MESSAGES = {
    "email": "I didn't quite catch that. Could you please type your email address? For example: name@email.com",
    "mobile_phone": "Sorry, could you share your mobile number again? For example: +91-XXXXX-XXXXX",
    "school_name": "Could you repeat the school name for {student}? For example: Lincoln Elementary School",
    "preferred_weekday": "Sorry, which day of the week works best? For example: Monday, Tuesday, etc.",
    "preferred_time": "Could you share the preferred time again? For example: 4:30 PM or 10 AM",
    "notes": "Any notes about {student}? Just type 'none' if there's nothing special to mention.",
}

HUMAN_HANDOFF_MESSAGE = (
    "Thank you for your patience! 🙏 I'm having a little difficulty — "
    "let me connect you with one of our team members who will reach out shortly. "
    "Have a great day!"
)

THANK_YOU_MESSAGE = (
    "That's everything I needed! Thank you so much, {parent_name}. 🎉\n\n"
    "We'll be in touch soon about {student_name}'s group event. Have a wonderful day!"
)

OPT_OUT_MESSAGE = (
    "I completely understand. We've noted your preference and won't contact you again. "
    "Sorry for the inconvenience! Take care. 👋"
)

CALLBACK_MESSAGE = (
    "No problem at all! We'll reach out again at a better time. Thanks, {parent_name}! 😊"
)


class WhatsAppConversationAgent:
    """
    Manages real multi-turn WhatsApp conversations with parents.
    Field-by-field approach with 2 retries per field.
    """

    def __init__(self, target_phone: Optional[str] = None):
        self.twilio = TwilioWhatsAppClient()
        self.target_phone = target_phone or config.YOUR_PHONE_NUMBER

        # Try to init OpenAI — fall back to regex if unavailable
        self.openai_client = None
        try:
            from openai import OpenAI
            if config.OPENAI_API_KEY:
                self.openai_client = OpenAI(api_key=config.OPENAI_API_KEY)
                # Quick validation
                logger.info("OpenAI client initialized")
        except Exception as e:
            logger.warning(f"OpenAI init failed: {e}. Using regex fallback.")

        self.sheets = GoogleSheetsClient()
        self.validator = DataValidator()
        self.audit = AuditLogger()
        self.idempotency = IdempotencyManager()
        self.results: list[OutreachResult] = []

    # ──────────────────────────────────────────────────
    #  Main Conversation Loop
    # ──────────────────────────────────────────────────

    def run_conversation(self, parent: ParentRecord) -> OutreachResult:
        """
        Execute a full WhatsApp conversation with a parent.
        Goes field by field, retries up to 2 times per field.
        """
        parent_id = parent.parent_id
        missing_fields = parent.get_missing_fields()
        known_fields = parent.get_known_fields()
        collected_fields: dict[str, str] = {}
        conversation_log: list[dict[str, str]] = []
        validation_flags: list[str] = []

        logger.info(f"\n{'='*60}")
        logger.info(f"WhatsApp Conversation: {parent.parent_name}")
        logger.info(f"  Phone: {self.target_phone}")
        logger.info(f"  Missing: {missing_fields}")
        logger.info(f"  Known: {known_fields}")
        logger.info(f"{'='*60}")

        # ── Idempotency ──
        if not self.idempotency.check_and_set(parent_id, "whatsapp_conv"):
            logger.warning(f"Duplicate conversation for {parent_id} today.")
            return self._build_result(parent, {}, OutreachStatus.COMPLETED,
                                      1.0, "skipped_duplicate", [], [], "Duplicate.")

        # ── Send greeting ──
        greeting = (
            f"Hi {parent.parent_name}! 👋 This is Alex from Bright Futures Academy.\n\n"
            f"We're setting up a group event for {parent.student_name} "
            f"and need a few quick details from you. It'll only take a minute!"
        )
        self._send(greeting, conversation_log)
        send_time = datetime.now(timezone.utc)

        self.audit.log(
            action="whatsapp_started", entity_type="parent",
            entity_id=parent_id, channel="whatsapp", status="in_progress",
        )

        # ── Go field by field ──
        final_status = OutreachStatus.COMPLETED
        escalated = False

        for field in missing_fields:
            # Ask the question
            question = FIELD_QUESTIONS[field].format(student=parent.student_name)
            self._send(question, conversation_log)
            send_time = datetime.now(timezone.utc)

            retries = 0
            max_retries = 2
            field_collected = False

            while retries <= max_retries:
                # Wait for reply
                reply = self.twilio.get_latest_reply(
                    from_number=self.target_phone,
                    after_timestamp=send_time,
                    timeout_seconds=config.WHATSAPP_TIMEOUT,
                    poll_interval=config.WHATSAPP_POLL_INTERVAL,
                )

                if reply is None:
                    # No reply at all — timeout
                    logger.warning(f"  No reply for field '{field}' (timeout)")
                    final_status = OutreachStatus.UNREACHABLE
                    escalated = True
                    break

                parent_msg = reply["body"].strip()
                conversation_log.append({"role": "parent", "content": parent_msg})
                logger.info(f"  Parent: {parent_msg}")

                # ── Check for special intents ──
                intent = self._detect_intent(parent_msg)
                if intent == "opt_out":
                    self._send(OPT_OUT_MESSAGE, conversation_log)
                    return self._build_result(parent, collected_fields,
                                              OutreachStatus.OPT_OUT, 0.95,
                                              "opt_out", conversation_log, validation_flags,
                                              "Parent opted out.")

                if intent == "callback":
                    msg = CALLBACK_MESSAGE.format(parent_name=parent.parent_name)
                    self._send(msg, conversation_log)
                    return self._build_result(parent, collected_fields,
                                              OutreachStatus.CALLBACK_REQUESTED, 0.85,
                                              "callback_requested", conversation_log,
                                              validation_flags, "Parent asked for callback.")

                if intent == "wrong_number":
                    self._send("I apologize for the confusion! Sorry to bother you. 🙏", conversation_log)
                    return self._build_result(parent, collected_fields,
                                              OutreachStatus.WRONG_NUMBER, 0.95,
                                              "wrong_number", conversation_log,
                                              validation_flags, "Wrong number.")

                # ── Try to extract the field value ──
                extracted_value = self._extract_field(field, parent_msg)

                if extracted_value:
                    # Validate (non-LLM)
                    cleaned, flags = self.validator.validate_all({field: extracted_value})
                    if flags:
                        validation_flags.extend(flags)
                        logger.warning(f"  Validation flags: {flags}")

                    collected_fields[field] = cleaned.get(field, extracted_value)
                    field_collected = True
                    logger.info(f"  ✅ Collected {field} = {collected_fields[field]}")

                    # Acknowledge
                    self._send(f"Got it, thanks! ✅", conversation_log)
                    send_time = datetime.now(timezone.utc)
                    break

                else:
                    # Couldn't extract — retry
                    retries += 1
                    if retries <= max_retries:
                        retry_msg = RETRY_MESSAGES[field].format(student=parent.student_name)
                        logger.info(f"  ❌ Retry {retries}/{max_retries} for '{field}'")
                        self._send(retry_msg, conversation_log)
                        send_time = datetime.now(timezone.utc)
                    else:
                        logger.warning(f"  ❌ Max retries for '{field}'. Escalating.")
                        escalated = True

            if escalated:
                break

        # ── End conversation ──
        if escalated:
            self._send(HUMAN_HANDOFF_MESSAGE, conversation_log)
            final_status = OutreachStatus.HUMAN_REVIEW
            notes = f"Escalated to human. Collected {len(collected_fields)}/{len(missing_fields)} fields."
            last_action = "escalated_to_human"
            confidence = 0.4
        else:
            thank_msg = THANK_YOU_MESSAGE.format(
                parent_name=parent.parent_name,
                student_name=parent.student_name,
            )
            self._send(thank_msg, conversation_log)
            final_status = OutreachStatus.COMPLETED
            notes = f"All {len(collected_fields)} fields collected successfully."
            last_action = "conversation_completed"
            confidence = 0.95

        result = self._build_result(
            parent, collected_fields, final_status, confidence,
            last_action, conversation_log, validation_flags, notes
        )

        # ── Write to Google Sheets ──
        self.sheets.write_outreach_result(result)
        self.audit.log(
            action="whatsapp_completed", entity_type="parent",
            entity_id=parent_id, channel="whatsapp", status=final_status.value,
            details=f"Collected {len(collected_fields)}/{len(missing_fields)} fields",
        )

        self.results.append(result)
        self._print_summary(result, len(missing_fields))
        return result

    # ──────────────────────────────────────────────────
    #  Send message helper
    # ──────────────────────────────────────────────────

    def _send(self, message: str, log: list[dict]):
        """Send a WhatsApp message and log it."""
        self.twilio.send_message(self.target_phone, message)
        log.append({"role": "agent", "content": message})
        # Small delay to avoid rate limits
        time.sleep(1)

    # ──────────────────────────────────────────────────
    #  Intent Detection (non-LLM, fast)
    # ──────────────────────────────────────────────────

    def _detect_intent(self, message: str) -> Optional[str]:
        """Detect special intents from parent message."""
        msg = message.lower().strip()

        opt_out_patterns = [
            "stop", "don't contact", "not interested", "opt out",
            "remove me", "unsubscribe", "leave me alone", "don't message",
            "please stop", "remove my number",
        ]
        if any(p in msg for p in opt_out_patterns):
            return "opt_out"

        callback_patterns = [
            "call me later", "call back", "busy right now", "not now",
            "contact me later", "later please", "i'm busy", "call me tomorrow",
            "can't talk", "in a meeting",
        ]
        if any(p in msg for p in callback_patterns):
            return "callback"

        wrong_number_patterns = [
            "wrong number", "wrong person", "who is this",
            "don't know", "no such person", "i'm not",
        ]
        if any(p in msg for p in wrong_number_patterns):
            return "wrong_number"

        return None

    # ──────────────────────────────────────────────────
    #  Field Extraction — LLM with regex fallback
    # ──────────────────────────────────────────────────

    def _extract_field(self, field_name: str, message: str) -> Optional[str]:
        """
        Extract a specific field value from the parent's message.
        Uses LLM if available, otherwise falls back to regex.
        """
        # Try LLM first
        if self.openai_client:
            try:
                return self._extract_with_llm(field_name, message)
            except Exception as e:
                logger.warning(f"LLM extraction failed: {e}. Using regex fallback.")

        # Regex fallback
        return self._extract_with_regex(field_name, message)

    def _extract_with_llm(self, field_name: str, message: str) -> Optional[str]:
        """Use OpenAI to extract a field value from a message."""
        prompt = f"""Extract the {field_name.replace('_', ' ')} from this message.
If the message contains a valid {field_name.replace('_', ' ')}, return it.
If the message does NOT contain it or is unclear, return null.

Message: "{message}"

Respond with ONLY a JSON object: {{"value": "extracted_value"}} or {{"value": null}}"""

        response = self.openai_client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "Extract data from messages. Return only JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=100,
            response_format={"type": "json_object"},
        )

        result = json.loads(response.choices[0].message.content)
        value = result.get("value")

        if value and str(value).lower() not in ("null", "none", "n/a", ""):
            return str(value)
        return None

    def _extract_with_regex(self, field_name: str, message: str) -> Optional[str]:
        """Regex-based extraction as fallback when LLM is unavailable."""
        msg = message.strip()

        if field_name == "email":
            match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', msg)
            return match.group() if match else None

        elif field_name == "mobile_phone":
            match = re.search(r'[\+]?[\d][\d\-\(\)\s]{6,14}[\d]', msg)
            return match.group().strip() if match else None

        elif field_name == "school_name":
            # Accept any non-trivial text as school name
            cleaned = msg.strip().strip('"\'').strip()
            if len(cleaned) >= 3 and not cleaned.lower() in ("no", "nah", "idk", "don't know"):
                return cleaned.title()
            return None

        elif field_name == "preferred_weekday":
            days = {
                "monday": "Monday", "mon": "Monday",
                "tuesday": "Tuesday", "tue": "Tuesday", "tues": "Tuesday",
                "wednesday": "Wednesday", "wed": "Wednesday",
                "thursday": "Thursday", "thu": "Thursday", "thurs": "Thursday",
                "friday": "Friday", "fri": "Friday",
                "saturday": "Saturday", "sat": "Saturday",
                "sunday": "Sunday", "sun": "Sunday",
            }
            msg_lower = msg.lower()
            for key, value in days.items():
                if key in msg_lower:
                    return value
            return None

        elif field_name == "preferred_time":
            match = re.search(r'\d{1,2}(?::\d{2})?\s*(?:am|pm|AM|PM)', msg)
            if match:
                return match.group()
            # Try 24h format
            match = re.search(r'(?:[01]?\d|2[0-3]):[0-5]\d', msg)
            if match:
                return match.group()
            # Informal
            msg_lower = msg.lower()
            if "morning" in msg_lower:
                return "10:00 AM"
            if "afternoon" in msg_lower:
                return "2:00 PM"
            if "evening" in msg_lower:
                return "5:00 PM"
            return None

        elif field_name == "notes":
            cleaned = msg.strip()
            if cleaned.lower() in ("none", "no", "nope", "n/a", "nothing", "na", "nil"):
                return "No special notes"
            if len(cleaned) >= 2:
                return cleaned
            return None

        return None

    # ──────────────────────────────────────────────────
    #  Build Result
    # ──────────────────────────────────────────────────

    def _build_result(
        self, parent, collected_fields, status, confidence,
        last_action, conv_log, val_flags, notes
    ) -> OutreachResult:
        return OutreachResult(
            parent_id=parent.parent_id,
            parent_name=parent.parent_name,
            student_name=parent.student_name,
            collected_fields=collected_fields,
            outreach_status=status,
            confidence=round(confidence, 2),
            last_action_taken=last_action,
            next_recommended_action=self._next_action(status),
            timestamp=datetime.now(),
            attempt_number=parent.attempt_count + 1,
            channel=OutreachChannel.WHATSAPP,
            conversation_log=conv_log,
            validation_flags=val_flags,
            notes=notes,
        )

    def _next_action(self, status: OutreachStatus) -> str:
        return {
            OutreachStatus.COMPLETED: "none",
            OutreachStatus.PARTIAL: "schedule_follow_up",
            OutreachStatus.CALLBACK_REQUESTED: "schedule_callback",
            OutreachStatus.UNREACHABLE: "retry_tomorrow",
            OutreachStatus.WRONG_NUMBER: "verify_phone_manually",
            OutreachStatus.OPT_OUT: "remove_from_list",
            OutreachStatus.HUMAN_REVIEW: "assign_to_human_agent",
        }.get(status, "unknown")

    def _print_summary(self, result: OutreachResult, total_missing: int):
        """Print a clean summary."""
        logger.info(f"\n{'='*60}")
        logger.info(f"DONE: {result.parent_name}")
        logger.info(f"  Status: {result.outreach_status.value}")
        logger.info(f"  Collected: {len(result.collected_fields)}/{total_missing}")
        logger.info(f"  Confidence: {result.confidence}")
        logger.info(f"  Fields: {result.collected_fields}")
        logger.info(f"  Notes: {result.notes}")
        logger.info(f"{'='*60}")

    def save_conversation_output(self):
        """Save results to JSON."""
        config.OUTPUTS_DIR.mkdir(exist_ok=True)
        results_json = [
            {
                "parent_id": r.parent_id,
                "parent_name": r.parent_name,
                "student_name": r.student_name,
                "collected_fields": r.collected_fields,
                "outreach_status": r.outreach_status.value,
                "confidence": r.confidence,
                "last_action_taken": r.last_action_taken,
                "next_recommended_action": r.next_recommended_action,
                "timestamp": r.timestamp.isoformat(),
                "channel": r.channel.value,
                "validation_flags": r.validation_flags,
                "notes": r.notes,
                "conversation_log": r.conversation_log,
            }
            for r in self.results
        ]
        path = config.OUTPUTS_DIR / "whatsapp_conversation_results.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(results_json, f, indent=2, ensure_ascii=False)
        logger.info(f"Results saved to {path}")
