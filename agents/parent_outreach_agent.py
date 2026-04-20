"""
Parent Outreach Voice Agent — Part 1
Main orchestrator that coordinates voice calls, WhatsApp follow-ups,
data collection, validation, and Google Sheets integration.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import config
from models.parent import (
    ParentRecord,
    OutreachResult,
    OutreachStatus,
    OutreachChannel,
    OutreachAttempt,
)
from agents.llm_client import LLMClient
from integrations.google_sheets import GoogleSheetsClient
from integrations.voice_simulator import VoiceSimulator
from integrations.whatsapp_simulator import WhatsAppSimulator
from reliability.validation import DataValidator
from reliability.audit import AuditLogger
from reliability.idempotency import IdempotencyManager

logger = logging.getLogger(__name__)


class ParentOutreachAgent:
    """
    AI Agent that:
    1. Calls parents to collect missing student information
    2. Sends WhatsApp follow-ups when calls go unanswered
    3. Validates collected data (non-LLM)
    4. Writes structured results to Google Sheets
    5. Records all outreach attempts with audit trail
    6. Handles idempotency (no duplicate rows or follow-ups)
    7. Routes low-confidence cases to human review
    """

    def __init__(self):
        self.llm = LLMClient()
        self.sheets = GoogleSheetsClient()
        self.voice = VoiceSimulator()
        self.whatsapp = WhatsAppSimulator()
        self.validator = DataValidator()
        self.audit = AuditLogger()
        self.idempotency = IdempotencyManager()
        self.results: list[OutreachResult] = []

    # ──────────────────────────────────────────────────
    #  Load Data
    # ──────────────────────────────────────────────────

    def load_parents(self, filepath: Optional[Path] = None) -> list[ParentRecord]:
        """Load parent records from JSON file."""
        filepath = filepath or config.DATA_DIR / "parents.json"
        with open(filepath, "r") as f:
            raw = json.load(f)

        parents = []
        for item in raw:
            # Remove scenario metadata before creating model
            scenario = item.pop("_scenario", "answers_all")
            item.pop("_scenario_description", "")
            parent = ParentRecord(**item)
            parent._scenario = scenario  # Attach for simulation
            parents.append(parent)

        logger.info(f"Loaded {len(parents)} parent records from {filepath}")
        return parents

    # ──────────────────────────────────────────────────
    #  Main Outreach Flow
    # ──────────────────────────────────────────────────

    async def run_outreach(self, parent: ParentRecord) -> OutreachResult:
        """
        Execute the full outreach flow for a single parent:
        1. Check idempotency
        2. Check opt-out
        3. Attempt voice call
        4. If no answer → WhatsApp follow-up
        5. Validate collected data
        6. Write to Google Sheets
        7. Log audit trail
        """
        start_time = time.time()
        parent_id = parent.parent_id
        scenario = getattr(parent, "_scenario", "answers_all")

        logger.info(f"\n{'='*60}")
        logger.info(f"Starting outreach for {parent.parent_name} ({parent_id})")
        logger.info(f"Scenario: {scenario}")
        logger.info(f"Missing fields: {parent.get_missing_fields()}")
        logger.info(f"Known fields: {parent.get_known_fields()}")
        logger.info(f"{'='*60}")

        # ── Step 1: Idempotency check ──
        if not self.idempotency.check_and_set(parent_id, "voice"):
            logger.warning(f"⚠️  Duplicate outreach for {parent_id} today. Skipping.")
            self.audit.log(
                action="outreach_skipped",
                entity_type="parent",
                entity_id=parent_id,
                channel="voice",
                status="duplicate",
                details="Idempotency check failed — already processed today",
            )
            return OutreachResult(
                parent_id=parent_id,
                parent_name=parent.parent_name,
                student_name=parent.student_name,
                outreach_status=OutreachStatus.COMPLETED,
                confidence=1.0,
                last_action_taken="skipped_duplicate",
                next_recommended_action="none",
                notes="Duplicate outreach detected. Skipped.",
            )

        # ── Step 2: Opt-out check ──
        if parent.opt_out:
            logger.info(f"🚫 {parent.parent_name} has opted out. Skipping.")
            self.audit.log(
                action="outreach_skipped",
                entity_type="parent",
                entity_id=parent_id,
                channel="voice",
                status="opt_out",
                details="Parent has opted out of communications",
            )
            return OutreachResult(
                parent_id=parent_id,
                parent_name=parent.parent_name,
                student_name=parent.student_name,
                outreach_status=OutreachStatus.OPT_OUT,
                confidence=1.0,
                last_action_taken="opt_out_check",
                next_recommended_action="none",
                notes="Parent opted out. No contact attempted.",
            )

        # ── Step 3: Voice call ──
        self.audit.log(
            action="voice_call_started",
            entity_type="parent",
            entity_id=parent_id,
            channel="voice",
            status="in_progress",
            details=f"Calling {parent.phone}",
        )

        call_result = await self.voice.make_call(parent, scenario=scenario)

        if not call_result["answered"]:
            # ── Step 4: WhatsApp follow-up ──
            result = await self._handle_no_answer(parent)
        else:
            # ── Process the conversation ──
            result = await self._process_voice_conversation(parent, call_result)

        # ── Step 5: Write to Google Sheets ──
        duration_ms = (time.time() - start_time) * 1000
        result.timestamp = datetime.now()

        written = self.sheets.write_outreach_result(result)
        if written:
            logger.info(f"✅ Result written to Google Sheets for {parent_id}")
            self.audit.log(
                action="sheets_write",
                entity_type="parent",
                entity_id=parent_id,
                status="success",
                details=f"Status: {result.outreach_status.value}",
                duration_ms=duration_ms,
            )
        else:
            logger.warning(f"⚠️  Duplicate sheet entry prevented for {parent_id}")

        self.results.append(result)
        return result

    # ──────────────────────────────────────────────────
    #  Voice Conversation Processing
    # ──────────────────────────────────────────────────

    async def _process_voice_conversation(
        self, parent: ParentRecord, call_result: dict
    ) -> OutreachResult:
        """Process a voice call conversation and extract data."""
        parent_id = parent.parent_id
        missing_fields = parent.get_missing_fields()
        known_fields = parent.get_known_fields()
        conversation = call_result["conversation"]

        # Generate agent greeting
        greeting = await self.llm.generate_agent_greeting(
            parent_name=parent.parent_name,
            student_name=parent.student_name,
            missing_fields=missing_fields,
            known_fields=known_fields,
        )

        # Build full conversation with agent greeting
        full_conversation = [{"role": "agent", "content": greeting}]
        full_conversation.extend(conversation)

        # Process with LLM
        llm_result = await self.llm.process_parent_response(
            conversation_history=full_conversation,
            missing_fields=missing_fields,
            parent_name=parent.parent_name,
            student_name=parent.student_name,
        )

        extracted = llm_result.get("extracted_fields", {})
        call_status = llm_result.get("call_status", "continue")
        confidence = llm_result.get("confidence", 0.5)
        needs_review = llm_result.get("needs_human_review", False)

        # ── Non-LLM Validation Step ──
        if extracted:
            cleaned_fields, validation_flags = self.validator.validate_all(extracted)
            confidence_adj = self.validator.calculate_confidence_adjustment(validation_flags)
            confidence *= confidence_adj

            if validation_flags:
                logger.warning(f"⚠️  Validation flags for {parent_id}: {validation_flags}")
        else:
            cleaned_fields = {}
            validation_flags = []

        # ── Map call status to OutreachStatus ──
        status_map = {
            "completed": OutreachStatus.COMPLETED,
            "continue": OutreachStatus.PARTIAL,
            "partial": OutreachStatus.PARTIAL,
            "callback": OutreachStatus.CALLBACK_REQUESTED,
            "wrong_number": OutreachStatus.WRONG_NUMBER,
            "opt_out": OutreachStatus.OPT_OUT,
        }
        outreach_status = status_map.get(call_status, OutreachStatus.PARTIAL)

        # ── Human review routing ──
        if needs_review or confidence < config.LOW_CONFIDENCE_THRESHOLD:
            outreach_status = OutreachStatus.HUMAN_REVIEW
            logger.info(f"🔍 Routing {parent_id} to human review (confidence: {confidence:.2f})")

        # ── Determine next action ──
        next_action = self._determine_next_action(outreach_status, call_status)

        # ── Build result ──
        self.audit.log(
            action="voice_call_completed",
            entity_type="parent",
            entity_id=parent_id,
            channel="voice",
            status=outreach_status.value,
            details=f"Collected {len(cleaned_fields)} fields. Confidence: {confidence:.2f}",
            duration_ms=call_result["duration_seconds"] * 1000,
            metadata={"extracted": cleaned_fields, "flags": validation_flags},
        )

        return OutreachResult(
            parent_id=parent_id,
            parent_name=parent.parent_name,
            student_name=parent.student_name,
            collected_fields=cleaned_fields,
            outreach_status=outreach_status,
            confidence=round(confidence, 2),
            last_action_taken=f"voice_call_{call_status}",
            next_recommended_action=next_action,
            attempt_number=parent.attempt_count + 1,
            channel=OutreachChannel.VOICE,
            conversation_log=full_conversation,
            validation_flags=validation_flags,
            notes=llm_result.get("agent_reply", ""),
        )

    # ──────────────────────────────────────────────────
    #  No Answer → WhatsApp Follow-up
    # ──────────────────────────────────────────────────

    async def _handle_no_answer(self, parent: ParentRecord) -> OutreachResult:
        """Handle when a parent doesn't answer: send WhatsApp follow-up."""
        parent_id = parent.parent_id
        missing_fields = parent.get_missing_fields()

        logger.info(f"📵 No answer from {parent.parent_name}. Sending WhatsApp follow-up...")

        self.audit.log(
            action="voice_call_no_answer",
            entity_type="parent",
            entity_id=parent_id,
            channel="voice",
            status="unreachable",
            details="Call went unanswered after 30 seconds",
        )

        # Check WhatsApp idempotency
        if not self.idempotency.check_and_set(parent_id, "whatsapp"):
            logger.warning(f"⚠️  WhatsApp already sent to {parent_id} today. Skipping duplicate.")
            return OutreachResult(
                parent_id=parent_id,
                parent_name=parent.parent_name,
                student_name=parent.student_name,
                outreach_status=OutreachStatus.UNREACHABLE,
                confidence=1.0,
                last_action_taken="whatsapp_skipped_duplicate",
                next_recommended_action="retry_call_tomorrow",
            )

        # Generate WhatsApp message
        wa_message = await self.llm.generate_whatsapp_message(
            parent_name=parent.parent_name,
            student_name=parent.student_name,
            missing_fields=missing_fields,
        )

        # Send WhatsApp
        wa_result = await self.whatsapp.send_message(
            phone=parent.phone,
            message=wa_message,
            parent_id=parent_id,
            parent_name=parent.parent_name,
        )

        # Log to Google Sheets WhatsApp tab
        self.sheets.log_whatsapp_message(
            parent_id=parent_id,
            parent_name=parent.parent_name,
            phone=parent.phone,
            message=wa_message,
            status="sent" if wa_result["success"] else "failed",
        )

        self.audit.log(
            action="whatsapp_sent",
            entity_type="parent",
            entity_id=parent_id,
            channel="whatsapp",
            status="sent" if wa_result["success"] else "failed",
            details=f"Message ID: {wa_result['message_id']}",
        )

        return OutreachResult(
            parent_id=parent_id,
            parent_name=parent.parent_name,
            student_name=parent.student_name,
            outreach_status=OutreachStatus.UNREACHABLE,
            confidence=1.0,
            last_action_taken="whatsapp_followup_sent",
            next_recommended_action="wait_for_whatsapp_reply_then_retry_call",
            channel=OutreachChannel.WHATSAPP,
            notes=f"WhatsApp sent: {wa_message[:100]}...",
        )

    # ──────────────────────────────────────────────────
    #  Helpers
    # ──────────────────────────────────────────────────

    def _determine_next_action(self, status: OutreachStatus, call_status: str) -> str:
        """Determine the recommended next action based on outreach result."""
        action_map = {
            OutreachStatus.COMPLETED: "none",
            OutreachStatus.PARTIAL: "schedule_follow_up_call",
            OutreachStatus.CALLBACK_REQUESTED: "schedule_callback_at_requested_time",
            OutreachStatus.UNREACHABLE: "retry_call_tomorrow",
            OutreachStatus.WRONG_NUMBER: "verify_phone_number_manually",
            OutreachStatus.OPT_OUT: "remove_from_outreach_list",
            OutreachStatus.HUMAN_REVIEW: "assign_to_human_agent",
        }
        return action_map.get(status, "unknown")

    # ──────────────────────────────────────────────────
    #  Run All Scenarios
    # ──────────────────────────────────────────────────

    async def run_all_demo_scenarios(self) -> list[OutreachResult]:
        """Run all demo scenarios and return results."""
        parents = self.load_parents()
        results = []

        for parent in parents:
            result = await self.run_outreach(parent)
            results.append(result)
            logger.info(
                f"\n📊 Result: {parent.parent_name} → "
                f"Status: {result.outreach_status.value} | "
                f"Confidence: {result.confidence:.2f} | "
                f"Collected: {len(result.collected_fields)} fields | "
                f"Next: {result.next_recommended_action}"
            )

        return results

    def get_summary_report(self) -> str:
        """Generate a human-readable summary of all outreach results."""
        lines = [
            "",
            "=" * 80,
            "PARENT OUTREACH SUMMARY REPORT",
            f"Generated: {datetime.now().isoformat()}",
            "=" * 80,
        ]

        for i, result in enumerate(self.results, 1):
            lines.extend([
                f"\n{'─'*60}",
                f"Scenario {i}: {result.parent_name} (Student: {result.student_name})",
                f"{'─'*60}",
                f"  Status:       {result.outreach_status.value}",
                f"  Channel:      {result.channel.value}",
                f"  Confidence:   {result.confidence:.2f}",
                f"  Collected:    {json.dumps(result.collected_fields, indent=2) if result.collected_fields else 'None'}",
                f"  Flags:        {', '.join(result.validation_flags) if result.validation_flags else 'None'}",
                f"  Last Action:  {result.last_action_taken}",
                f"  Next Action:  {result.next_recommended_action}",
                f"  Timestamp:    {result.timestamp.isoformat()}",
                f"  Notes:        {result.notes[:100] if result.notes else 'N/A'}",
            ])

        # Status distribution
        status_counts = {}
        for r in self.results:
            s = r.outreach_status.value
            status_counts[s] = status_counts.get(s, 0) + 1

        lines.extend([
            f"\n{'='*60}",
            "STATUS DISTRIBUTION",
            "=" * 60,
        ])
        for status, count in status_counts.items():
            lines.append(f"  {status:25s} : {count}")

        lines.extend([
            f"\n  Total Outreach Attempts:  {len(self.results)}",
            f"  Google Sheets Rows Written: {len(self.results)}",
            f"  WhatsApp Messages Sent: {len(self.whatsapp.get_sent_messages())}",
            "=" * 80,
        ])

        return "\n".join(lines)

    def save_output_files(self):
        """Save results to output files for demo/documentation."""
        config.OUTPUTS_DIR.mkdir(exist_ok=True)

        # Save structured JSON results
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
                "attempt_number": r.attempt_number,
                "channel": r.channel.value,
                "validation_flags": r.validation_flags,
                "notes": r.notes,
                "conversation_log": r.conversation_log,
            }
            for r in self.results
        ]

        output_path = config.OUTPUTS_DIR / "outreach_results.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results_json, f, indent=2, ensure_ascii=False)
        logger.info(f"Results saved to {output_path}")

        # Save summary report
        report_path = config.OUTPUTS_DIR / "outreach_summary.txt"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(self.get_summary_report())
        logger.info(f"Summary report saved to {report_path}")

        # Save Google Sheets simulation data
        if config.SIMULATION_MODE:
            sim_path = config.OUTPUTS_DIR / "google_sheets_simulation.json"
            with open(sim_path, "w", encoding="utf-8") as f:
                json.dump(self.sheets.get_simulation_data(), f, indent=2, default=str, ensure_ascii=False)
            logger.info(f"Sheets simulation data saved to {sim_path}")

        # Save audit log
        audit_path = config.OUTPUTS_DIR / "audit_log.txt"
        with open(audit_path, "w", encoding="utf-8") as f:
            f.write(self.audit.get_all_logs_formatted())
        logger.info(f"Audit log saved to {audit_path}")

