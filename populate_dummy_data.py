import json
from datetime import datetime, timedelta
import random
from integrations.google_sheets import GoogleSheetsClient, SHEET_PARENT_OUTREACH, SHEET_WHATSAPP_LOG, SHEET_AUDIT_LOG
from config import config

def populate():
    print("Initializing Google Sheets Client...")
    client = GoogleSheetsClient()
    
    print("Populating Students tab...")
    with open(config.DATA_DIR / "students.json", "r", encoding="utf-8") as f:
        students = json.load(f)
    
    # Save the 15 dummy students to the 'Students' sheet
    client.save_students(students)
    
    print("Populating Parent Outreach tab...")
    now = datetime.now()
    try:
        ws_outreach = client.spreadsheet.worksheet(SHEET_PARENT_OUTREACH)
        ws_outreach.clear()
        outreach_data = [
            ws_outreach.row_values(1) or [
                "Parent ID", "Parent Name", "Student Name", "Collected Fields", "Outreach Status", "Confidence", 
                "Last Action Taken", "Next Recommended Action", "Timestamp", "Attempt Number", "Channel", 
                "Validation Flags", "Notes"
            ]
        ]
        
        # Add 5 dummy outreach results
        for i, s in enumerate(students[:5]):
            outreach_data.append([
                s["student_id"],
                s["parent_name"],
                s["student_name"],
                "preferred_weekday=Monday",
                "completed" if i % 2 == 0 else "partial",
                "0.95",
                "voice_call_completed",
                "none",
                (now - timedelta(days=i)).isoformat(),
                "1",
                "voice",
                "none",
                "Parent was very helpful."
            ])
        ws_outreach.update(outreach_data)
        
        print("Populating WhatsApp Log tab...")
        ws_wa = client.spreadsheet.worksheet(SHEET_WHATSAPP_LOG)
        ws_wa.clear()
        wa_data = [
            ws_wa.row_values(1) or ["Timestamp", "Parent ID", "Parent Name", "Phone", "Message", "Status"]
        ]
        
        for i, s in enumerate(students[5:10]):
            wa_data.append([
                (now - timedelta(days=i, hours=2)).isoformat(),
                s["student_id"],
                s["parent_name"],
                s["parent_phone"],
                f"Hi {s['parent_name']}, we missed you during our call. Please reply to update {s['student_name']}'s info.",
                "sent" if i != 2 else "failed"
            ])
        ws_wa.update(wa_data)
        
        print("Populating Audit Log tab...")
        ws_audit = client.spreadsheet.worksheet(SHEET_AUDIT_LOG)
        ws_audit.clear()
        audit_data = [
            ws_audit.row_values(1) or ["Timestamp", "Action", "Parent ID", "Details", "Status"]
        ]
        
        for i in range(10):
            audit_data.append([
                (now - timedelta(hours=i)).isoformat(),
                "voice_call_started" if i % 3 == 0 else "whatsapp_sent",
                f"S00{i+1}" if i < 9 else f"S010",
                "Simulated action log",
                "success"
            ])
        ws_audit.update(audit_data)
        
        print("Successfully populated all tabs with dummy data!")
    except Exception as e:
        print(f"Error populating additional tabs: {e}")

if __name__ == "__main__":
    populate()
