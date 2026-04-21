"""
OpenAI LLM client wrapper with simulation fallback.
Provides structured output extraction for the parent outreach agent.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from config import config
from reliability.retry import with_retry

logger = logging.getLogger(__name__)


class LLMClient:
    """
    Wrapper around OpenAI API with simulation fallback.
    In simulation mode, returns predefined responses based on scenario.
    """

    def __init__(self):
        self.client = None
        self.simulation_mode = config.SIMULATION_MODE

        if not self.simulation_mode and config.OPENAI_API_KEY:
            try:
                from openai import OpenAI
                self.client = OpenAI(api_key=config.OPENAI_API_KEY)
                logger.info("OpenAI client initialized successfully")
            except Exception as e:
                logger.warning(f"Failed to init OpenAI client: {e}. Falling back to simulation.")
                self.simulation_mode = True
        else:
            if not self.simulation_mode:
                logger.warning("No OpenAI API key. Falling back to simulation mode.")
                self.simulation_mode = True

    async def generate_agent_greeting(
        self,
        parent_name: str,
        student_name: str,
        missing_fields: list[str],
        known_fields: dict[str, str],
    ) -> str:
        """Generate the opening greeting for a voice call."""
        prompt = f"""You are a friendly outreach coordinator calling parents to collect missing information 
about their child before scheduling a group event.

Parent: {parent_name}
Student: {student_name}
Already known: {json.dumps(known_fields) if known_fields else "Nothing besides contact number"}
Missing fields we need to collect: {', '.join(missing_fields)}

Generate a warm, professional opening greeting. Be concise. Only ask about the missing fields.
Do NOT ask about fields we already have. Introduce yourself as being from "Bright Futures Academy"."""

        if self.simulation_mode:
            missing_str = ", ".join(f.replace("_", " ") for f in missing_fields)
            return (
                f"Hi {parent_name}, this is Alex from Bright Futures Academy. "
                f"I'm calling about {student_name}'s upcoming group event. "
                f"We just need a few details from you — specifically your {missing_str}. "
                f"Do you have a moment?"
            )

        return await self._call_llm(prompt)

    async def process_parent_response(
        self,
        conversation_history: list[dict[str, str]],
        missing_fields: list[str],
        parent_name: str,
        student_name: str,
    ) -> dict:
        """
        Process a parent's response and extract structured data.
        Returns: {
            "extracted_fields": {"field_name": "value", ...},
            "agent_reply": "...",
            "call_status": "continue|completed|callback|wrong_number|opt_out",
            "confidence": 0.0-1.0
        }
        """
        conv_text = "\n".join(
            f"{'Agent' if m['role'] == 'agent' else 'Parent'}: {m['content']}"
            for m in conversation_history
        )

        prompt = f"""Analyze this phone conversation and extract information.

Parent: {parent_name} | Student: {student_name}
Missing fields: {', '.join(missing_fields)}

Conversation:
{conv_text}

Return a JSON object with:
- "extracted_fields": dict of field_name -> extracted value (only fields from the missing list)
- "agent_reply": what the agent should say next
- "call_status": one of "continue", "completed", "callback", "wrong_number", "opt_out", "partial"
- "confidence": float 0.0-1.0 indicating confidence in extracted data
- "needs_human_review": boolean

Only extract fields explicitly stated by the parent. Do NOT guess or infer."""

        if self.simulation_mode:
            return self._simulate_processing(conversation_history, missing_fields)

        response = await self._call_llm(prompt)
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            logger.error(f"Failed to parse LLM response as JSON: {response}")
            return {
                "extracted_fields": {},
                "agent_reply": "I'm sorry, could you repeat that?",
                "call_status": "continue",
                "confidence": 0.3,
                "needs_human_review": True,
            }

    async def generate_whatsapp_message(
        self,
        parent_name: str,
        student_name: str,
        missing_fields: list[str],
    ) -> str:
        """Generate a WhatsApp follow-up message."""
        prompt = f"""Generate a short, friendly WhatsApp message for {parent_name} about {student_name}.
We tried calling but couldn't reach them. We need: {', '.join(missing_fields)}.
Keep it under 160 characters if possible. Be professional but warm.
From: Bright Futures Academy."""

        if self.simulation_mode:
            missing_str = ", ".join(f.replace("_", " ") for f in missing_fields[:3])
            return (
                f"Hi {parent_name}! 👋 This is Bright Futures Academy. "
                f"We tried reaching you about {student_name}'s upcoming group event. "
                f"Could you please share your {missing_str}? "
                f"Reply here or call us back at your convenience. Thank you! 🙏"
            )

        return await self._call_llm(prompt)

    @with_retry(max_retries=3, base_delay=2.0)
    async def _call_llm(self, prompt: str) -> str:
        """Make an actual OpenAI API call."""
        if not self.client:
            raise RuntimeError("OpenAI client not initialized")

        try:
            response = self.client.chat.completions.create(
                model=config.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "You are a helpful outreach coordinator assistant."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.4,
                max_tokens=500,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            raise

    def _simulate_processing(
        self,
        conversation_history: list[dict[str, str]],
        missing_fields: list[str],
    ) -> dict:
        """Simulate LLM processing based on conversation content."""
        # Collect ALL parent messages for comprehensive analysis
        all_parent_msgs = []
        for msg in conversation_history:
            if msg["role"] == "parent":
                all_parent_msgs.append(msg["content"].lower())

        combined_parent_text = " ".join(all_parent_msgs)

        # Detect special statuses (check across all messages)
        if any(w in combined_parent_text for w in ["wrong number", "there's no", "no one by that name", "bob's auto shop"]):
            return {
                "extracted_fields": {},
                "agent_reply": "I apologize for the confusion. Thank you for letting me know. Have a good day!",
                "call_status": "wrong_number",
                "confidence": 0.95,
                "needs_human_review": False,
            }

        if any(w in combined_parent_text for w in ["stop calling", "don't call", "opt out", "remove my number", "not interested", "remove me"]):
            return {
                "extracted_fields": {},
                "agent_reply": "I completely understand. We'll remove you from our contact list. Sorry for the inconvenience. Goodbye!",
                "call_status": "opt_out",
                "confidence": 0.95,
                "needs_human_review": False,
            }

        if any(w in combined_parent_text for w in ["call me back", "call back", "call later", "in a meeting", "not now"]):
            return {
                "extracted_fields": {},
                "agent_reply": "No problem at all! When would be a good time to call you back?",
                "call_status": "callback",
                "confidence": 0.85,
                "needs_human_review": False,
            }

        # Check if parent hung up abruptly (partial scenario)
        last_parent_msg = all_parent_msgs[-1] if all_parent_msgs else ""
        abrupt_hangup = any(w in last_parent_msg for w in ["sorry", "need to take this", "gotta go", "hang up"])

        # Try to extract fields from ALL parent messages
        extracted = {}
        confidence = 0.9
        import re

        if "email" in missing_fields:
            for msg in all_parent_msgs:
                words = msg.split()
                for w in words:
                    if "@" in w and "." in w:
                        extracted["email"] = w.strip(".,!?")
                        break
                if "email" in extracted:
                    break

        if "school_name" in missing_fields:
            school_keywords = ["elementary school", "high school", "middle school", "academy", "institute"]
            for msg in all_parent_msgs:
                for kw in school_keywords:
                    if kw in msg:
                        # Extract words before the keyword as school name
                        parts = msg.split(kw)
                        prefix_text = parts[0].strip()
                        # Look for "goes to" or "attends" pattern to find the school name
                        for delimiter in ["goes to ", "attends ", "at ", "is "]:
                            if delimiter in prefix_text:
                                school_prefix = prefix_text.split(delimiter)[-1].strip()
                                extracted["school_name"] = school_prefix.title() + " " + kw.title()
                                break
                        else:
                            # Fallback: take last 1-2 words before keyword
                            words = prefix_text.split()
                            school_words = words[-1:] if words else []
                            if school_words:
                                extracted["school_name"] = " ".join(school_words).title() + " " + kw.title()
                            else:
                                extracted["school_name"] = kw.title()
                        break
                if "school_name" in extracted:
                    break

        if "preferred_weekday" in missing_fields:
            days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
            for msg in all_parent_msgs:
                for day in days:
                    if day in msg:
                        extracted["preferred_weekday"] = day.title()
                        break
                if "preferred_weekday" in extracted:
                    break

        if "preferred_time" in missing_fields:
            time_pattern = r'\d{1,2}(?::\d{2})?\s*(?:am|pm|AM|PM)'
            for msg in all_parent_msgs:
                match = re.search(time_pattern, msg)
                if match:
                    extracted["preferred_time"] = match.group()
                    break

        if "mobile_phone" in missing_fields:
            phone_pattern = r'[\+]?[\d][\d\-\(\)\s]{6,14}[\d]'
            for msg in all_parent_msgs:
                match = re.search(phone_pattern, msg)
                if match:
                    extracted["mobile_phone"] = match.group().strip()
                    break

        if "notes" in missing_fields:
            # Look for notes-like content (allergy, special needs, etc.)
            note_keywords = ["allergy", "allergic", "note", "please note", "special", "needs"]
            for msg in all_parent_msgs:
                for kw in note_keywords:
                    if kw in msg:
                        # Extract the sentence containing the keyword
                        sentences = msg.split(".")
                        for s in sentences:
                            if kw in s:
                                extracted["notes"] = s.strip().capitalize()
                                break
                        break
                if "notes" in extracted:
                    break

        # Determine remaining missing
        remaining = [f for f in missing_fields if f not in extracted]

        # Determine status based on what happened
        if abrupt_hangup:
            if extracted:
                status = "partial"
                reply = f"Call ended abruptly. Collected {len(extracted)} of {len(missing_fields)} fields."
            else:
                status = "partial"
                reply = "Call ended before any data could be collected."
            confidence = 0.6 if extracted else 0.4
        elif not remaining:
            status = "completed"
            reply = "Thank you so much! I have all the information I need. We'll be in touch about the group event. Have a wonderful day!"
            confidence = 0.95
        elif extracted:
            status = "partial"
            remaining_str = ", ".join(f.replace("_", " ") for f in remaining)
            reply = f"Thank you! Could you also provide your {remaining_str}?"
            confidence = 0.7
        else:
            status = "continue"
            reply = "I didn't quite catch that. Could you please repeat?"
            confidence = 0.5

        return {
            "extracted_fields": extracted,
            "agent_reply": reply,
            "call_status": status,
            "confidence": confidence,
            "needs_human_review": confidence < 0.5,
        }

