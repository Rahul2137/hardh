"""
Dashboard API — FastAPI backend for the Parent Outreach Dashboard.

Endpoints:
  GET  /api/students            — List all students from data store
  GET  /api/students/{id}       — Get single student
  POST /api/students/{id}/trigger — Trigger WhatsApp outreach for a student
  GET  /api/human-review        — Get conversations needing human review
  POST /api/jobs/daily-check    — Run the daily incomplete-info check
  GET  /api/conversations/{id}  — Get conversation log for a student
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from config import config

logger = logging.getLogger(__name__)

app = FastAPI(title="Parent Outreach Dashboard", version="1.0.0")

# CORS for local dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────── Data Store ───────────────

DATA_FILE = config.DATA_DIR / "students.json"
CONVERSATIONS_DIR = config.OUTPUTS_DIR / "conversations"
CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)

# Track active outreach jobs
active_jobs: dict[str, str] = {}  # student_id -> status


def load_students() -> list[dict]:
    """Load students from JSON data store."""
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_students(students: list[dict]):
    """Save students back to JSON data store."""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(students, f, indent=2, ensure_ascii=False)


def get_missing_fields(student: dict) -> list[str]:
    """Get list of missing fields for a student."""
    fields_to_check = [
        "parent_email", "school_name", "preferred_weekday",
        "preferred_time", "notes"
    ]
    return [f for f in fields_to_check if not student.get(f, "").strip()]


def is_complete(student: dict) -> bool:
    """Check if all required fields are filled."""
    return len(get_missing_fields(student)) == 0


def save_conversation(student_id: str, conversation: list[dict], result: dict):
    """Save a conversation log to file."""
    data = {
        "student_id": student_id,
        "timestamp": datetime.now().isoformat(),
        "result": result,
        "conversation": conversation,
    }
    path = CONVERSATIONS_DIR / f"{student_id}_conversation.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_conversation(student_id: str) -> Optional[dict]:
    """Load a saved conversation."""
    path = CONVERSATIONS_DIR / f"{student_id}_conversation.json"
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


# ─────────────── Background Outreach Worker ───────────────

def run_outreach_background(student_id: str):
    """Run WhatsApp outreach in a background thread."""
    from models.parent import ParentRecord
    from agents.whatsapp_conversation import WhatsAppConversationAgent

    try:
        active_jobs[student_id] = "running"
        students = load_students()
        student = next((s for s in students if s["student_id"] == student_id), None)

        if not student:
            active_jobs[student_id] = "error"
            return

        # Create a ParentRecord from student data
        parent = ParentRecord(
            parent_id=student_id,
            parent_name=student["parent_name"],
            student_name=student["student_name"],
            phone=student["parent_phone"],
            email=student.get("parent_email", ""),
            school_name=student.get("school_name", ""),
            preferred_weekday=student.get("preferred_weekday", ""),
            preferred_time=student.get("preferred_time", ""),
            notes=student.get("notes", ""),
            scenario="whatsapp_real",
            attempt_count=student.get("attempts", 0),
        )

        agent = WhatsAppConversationAgent(
            target_phone=f"whatsapp:{student['parent_phone']}"
            if not student["parent_phone"].startswith("whatsapp:")
            else student["parent_phone"]
        )
        result = agent.run_conversation(parent)

        # Update the student record with collected data
        students = load_students()  # Reload fresh
        for s in students:
            if s["student_id"] == student_id:
                # Update collected fields
                for field, value in result.collected_fields.items():
                    field_map = {
                        "email": "parent_email",
                        "mobile_phone": "parent_phone",
                        "school_name": "school_name",
                        "preferred_weekday": "preferred_weekday",
                        "preferred_time": "preferred_time",
                        "notes": "notes",
                    }
                    sheet_field = field_map.get(field, field)
                    if value and sheet_field in s:
                        s[sheet_field] = value

                s["attempts"] = s.get("attempts", 0) + 1
                s["last_contact"] = date.today().isoformat()
                s["status"] = result.outreach_status.value
                break

        save_students(students)

        # Save conversation log
        save_conversation(student_id, result.conversation_log, {
            "status": result.outreach_status.value,
            "confidence": result.confidence,
            "collected_fields": result.collected_fields,
            "notes": result.notes,
        })

        active_jobs[student_id] = "done"
        logger.info(f"Outreach for {student_id} completed: {result.outreach_status.value}")

    except Exception as e:
        logger.error(f"Outreach error for {student_id}: {e}")
        active_jobs[student_id] = f"error: {str(e)}"


# ─────────────── API Endpoints ───────────────

class StudentResponse(BaseModel):
    student_id: str
    student_name: str
    grade: str
    parent_name: str
    parent_phone: str
    parent_email: str
    school_name: str
    preferred_weekday: str
    preferred_time: str
    notes: str
    status: str
    attempts: int
    last_contact: str
    is_complete: bool
    missing_fields: list[str]
    job_status: Optional[str] = None


@app.get("/api/students")
async def list_students():
    """Get all students with their completion status."""
    students = load_students()
    result = []
    for s in students:
        result.append({
            **s,
            "is_complete": is_complete(s),
            "missing_fields": get_missing_fields(s),
            "job_status": active_jobs.get(s["student_id"]),
        })
    return {"students": result, "total": len(result)}


@app.get("/api/students/{student_id}")
async def get_student(student_id: str):
    """Get a single student."""
    students = load_students()
    student = next((s for s in students if s["student_id"] == student_id), None)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    return {
        **student,
        "is_complete": is_complete(student),
        "missing_fields": get_missing_fields(student),
        "job_status": active_jobs.get(student_id),
    }


@app.post("/api/students/{student_id}/trigger")
async def trigger_outreach(student_id: str):
    """Trigger WhatsApp outreach for a student."""
    students = load_students()
    student = next((s for s in students if s["student_id"] == student_id), None)

    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    if is_complete(student):
        raise HTTPException(status_code=400, detail="All fields already complete")

    if active_jobs.get(student_id) == "running":
        raise HTTPException(status_code=409, detail="Outreach already in progress")

    if not student.get("parent_phone"):
        raise HTTPException(status_code=400, detail="No parent phone number")

    # Run in background thread
    thread = threading.Thread(target=run_outreach_background, args=(student_id,))
    thread.daemon = True
    thread.start()

    return {
        "message": f"Outreach started for {student['student_name']}",
        "student_id": student_id,
        "status": "running",
    }


@app.get("/api/human-review")
async def get_human_review():
    """Get all conversations that need human review."""
    students = load_students()
    review_list = []

    for s in students:
        if s.get("status") in ("human_review", "unreachable") or s.get("attempts", 0) >= 3:
            conv = load_conversation(s["student_id"])
            review_list.append({
                **s,
                "missing_fields": get_missing_fields(s),
                "conversation": conv.get("conversation", []) if conv else [],
                "last_result": conv.get("result", {}) if conv else {},
            })

    return {"reviews": review_list, "total": len(review_list)}


@app.get("/api/conversations/{student_id}")
async def get_conversation(student_id: str):
    """Get conversation log for a student."""
    conv = load_conversation(student_id)
    if not conv:
        raise HTTPException(status_code=404, detail="No conversation found")
    return conv


@app.post("/api/jobs/daily-check")
async def daily_check():
    """
    Daily job: finds students with incomplete info + attempts < 3,
    and triggers outreach for them.
    """
    students = load_students()
    triggered = []
    skipped = []

    for s in students:
        sid = s["student_id"]

        # Skip if complete
        if is_complete(s):
            continue

        # Skip if already at max attempts
        if s.get("attempts", 0) >= 3:
            skipped.append({"student_id": sid, "reason": "max_attempts_reached"})
            continue

        # Skip if already running
        if active_jobs.get(sid) == "running":
            skipped.append({"student_id": sid, "reason": "already_running"})
            continue

        # Skip if opted out or wrong number
        if s.get("status") in ("opt_out", "wrong_number"):
            skipped.append({"student_id": sid, "reason": s["status"]})
            continue

        # Skip if no phone
        if not s.get("parent_phone"):
            skipped.append({"student_id": sid, "reason": "no_phone"})
            continue

        # Trigger outreach
        thread = threading.Thread(target=run_outreach_background, args=(sid,))
        thread.daemon = True
        thread.start()
        triggered.append(sid)

    return {
        "message": f"Daily check complete. Triggered {len(triggered)} outreach(es).",
        "triggered": triggered,
        "skipped": skipped,
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/api/stats")
async def get_stats():
    """Dashboard statistics."""
    students = load_students()
    total = len(students)
    completed = sum(1 for s in students if is_complete(s))
    pending = sum(1 for s in students if s.get("status") == "pending")
    partial = sum(1 for s in students if s.get("status") == "partial")
    human_review = sum(1 for s in students if s.get("status") in ("human_review", "unreachable"))
    opted_out = sum(1 for s in students if s.get("status") == "opt_out")

    return {
        "total": total,
        "completed": completed,
        "pending": pending,
        "partial": partial,
        "human_review": human_review,
        "opted_out": opted_out,
        "completion_rate": round(completed / total * 100, 1) if total else 0,
    }


# ─────────────── Serve Frontend ───────────────

FRONTEND_DIR = Path(__file__).parent / "frontend"

@app.get("/")
async def serve_frontend():
    """Serve the dashboard HTML."""
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/style.css")
async def serve_css():
    return FileResponse(FRONTEND_DIR / "style.css", media_type="text/css")


@app.get("/app.js")
async def serve_js():
    return FileResponse(FRONTEND_DIR / "app.js", media_type="application/javascript")
