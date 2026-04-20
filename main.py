"""
Main entry point for the AI Outreach System.
Provides both CLI and FastAPI server modes.

Usage:
    python main.py                    — Run all Part 1 demo scenarios
    python main.py --server           — Start FastAPI server
    python main.py --scenario 1       — Run a specific scenario (1-6)
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import os
import argparse
from datetime import datetime
from pathlib import Path

# Fix Windows console encoding for Unicode
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# Setup logging with rich formatting
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent))

from config import config


async def run_part1_demo():
    """Run all Part 1 (Parent Outreach) demo scenarios."""
    from agents.parent_outreach_agent import ParentOutreachAgent

    print("\n" + "🎯" * 30)
    print("  PART 1 — PARENT OUTREACH VOICE AGENT DEMO")
    print("🎯" * 30)
    print(f"\n  Mode: {'SIMULATION' if config.SIMULATION_MODE else 'PRODUCTION'}")
    print(f"  Time: {datetime.now().isoformat()}")
    print(f"  Data: {config.DATA_DIR / 'parents.json'}")
    print()

    agent = ParentOutreachAgent()

    # Run all demo scenarios
    results = await agent.run_all_demo_scenarios()

    # Print summary
    print(agent.get_summary_report())

    # Save outputs
    agent.save_output_files()

    # Print what was written to Google Sheets
    print("\n" + "=" * 60)
    print("GOOGLE SHEETS DATA (Simulated)")
    print("=" * 60)

    if config.SIMULATION_MODE:
        sim_data = agent.sheets.get_simulation_data()
        for sheet_name, rows in sim_data.items():
            print(f"\n📊 Sheet: {sheet_name}")
            print("-" * 40)
            if rows:
                headers = rows[0]
                for i, row in enumerate(rows[1:], 1):
                    print(f"\n  Row {i}:")
                    for h, v in zip(headers, row):
                        if v:
                            print(f"    {h:25s}: {v}")

    # Print WhatsApp messages
    wa_messages = agent.whatsapp.get_sent_messages()
    if wa_messages:
        print("\n" + "=" * 60)
        print("WHATSAPP MESSAGES SENT")
        print("=" * 60)
        for msg in wa_messages:
            print(f"\n  To: {msg['parent_name']} ({msg['phone']})")
            print(f"  Message ID: {msg['message_id']}")
            print(f"  Content: {msg['message']}")
            print(f"  Status: {msg['status']}")

    # Test idempotency — run again and verify no duplicates
    print("\n" + "=" * 60)
    print("IDEMPOTENCY TEST — Running same outreach again...")
    print("=" * 60)
    parents = agent.load_parents()
    for parent in parents[:2]:  # Test with first 2
        result = await agent.run_outreach(parent)
        print(f"  {parent.parent_name}: {result.last_action_taken} (should be 'skipped_duplicate')")

    print("\n✅ Part 1 Demo Complete!")
    print(f"📁 Output files saved to: {config.OUTPUTS_DIR}")

    return results


async def run_single_scenario(scenario_num: int):
    """Run a single demo scenario."""
    from agents.parent_outreach_agent import ParentOutreachAgent

    agent = ParentOutreachAgent()
    parents = agent.load_parents()

    if scenario_num < 1 or scenario_num > len(parents):
        print(f"Invalid scenario number. Choose 1-{len(parents)}")
        return

    parent = parents[scenario_num - 1]
    scenario = getattr(parent, "_scenario", "unknown")

    print(f"\n🎯 Running Scenario {scenario_num}: {parent.parent_name}")
    print(f"   Scenario type: {scenario}")
    print(f"   Missing fields: {parent.get_missing_fields()}")
    print()

    result = await agent.run_outreach(parent)

    print(f"\n📊 Result:")
    print(f"   Status: {result.outreach_status.value}")
    print(f"   Confidence: {result.confidence:.2f}")
    print(f"   Collected: {json.dumps(result.collected_fields, indent=2)}")
    print(f"   Next Action: {result.next_recommended_action}")
    print(f"   Validation Flags: {result.validation_flags}")

    agent.save_output_files()


def start_server():
    """Start the FastAPI server."""
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import JSONResponse
    import uvicorn

    app = FastAPI(
        title="AI Outreach System",
        description="Parent Outreach Voice Agent + Telegram Student Context Agent",
        version="1.0.0",
    )

    agent = None

    @app.on_event("startup")
    async def startup():
        nonlocal agent
        from agents.parent_outreach_agent import ParentOutreachAgent
        agent = ParentOutreachAgent()
        logger.info("FastAPI server started. Agent ready.")

    @app.get("/")
    async def root():
        return {"status": "ok", "message": "AI Outreach System", "mode": "simulation" if config.SIMULATION_MODE else "production"}

    @app.post("/api/v1/outreach/run-all")
    async def run_all():
        """Execute outreach for all parents."""
        results = await agent.run_all_demo_scenarios()
        return JSONResponse(content={
            "success": True,
            "results_count": len(results),
            "results": [
                {
                    "parent_id": r.parent_id,
                    "parent_name": r.parent_name,
                    "student_name": r.student_name,
                    "status": r.outreach_status.value,
                    "confidence": r.confidence,
                    "collected_fields": r.collected_fields,
                    "next_action": r.next_recommended_action,
                }
                for r in results
            ],
        })

    @app.post("/api/v1/outreach/{parent_id}")
    async def run_single(parent_id: str):
        """Execute outreach for a specific parent."""
        parents = agent.load_parents()
        parent = next((p for p in parents if p.parent_id == parent_id), None)
        if not parent:
            raise HTTPException(status_code=404, detail=f"Parent {parent_id} not found")

        result = await agent.run_outreach(parent)
        return JSONResponse(content={
            "parent_id": result.parent_id,
            "status": result.outreach_status.value,
            "confidence": result.confidence,
            "collected_fields": result.collected_fields,
            "next_action": result.next_recommended_action,
        })

    @app.get("/api/v1/outreach/results")
    async def get_results():
        """Get all outreach results."""
        return JSONResponse(content={
            "results": [
                {
                    "parent_id": r.parent_id,
                    "parent_name": r.parent_name,
                    "status": r.outreach_status.value,
                    "confidence": r.confidence,
                    "collected_fields": r.collected_fields,
                }
                for r in agent.results
            ]
        })

    @app.get("/api/v1/sheets/data")
    async def get_sheets_data():
        """Get Google Sheets data (simulation only)."""
        if config.SIMULATION_MODE:
            return JSONResponse(content=agent.sheets.get_simulation_data())
        return {"message": "Not available in production mode. Check Google Sheets directly."}

    @app.get("/api/v1/audit/logs")
    async def get_audit_logs():
        """Get audit logs."""
        logs = agent.audit.get_logs(limit=50)
        return JSONResponse(content={"logs": logs})

    uvicorn.run(app, host="0.0.0.0", port=8000)


# ──────────────────────────────────────────────────
#  CLI Entry Point
# ──────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AI Outreach System")
    parser.add_argument("--server", action="store_true", help="Start FastAPI server")
    parser.add_argument("--scenario", type=int, help="Run a specific scenario (1-6)")
    parser.add_argument("--production", action="store_true", help="Run in production mode")
    args = parser.parse_args()

    if args.production:
        config.SIMULATION_MODE = False
        config.validate()

    if args.server:
        start_server()
    elif args.scenario:
        asyncio.run(run_single_scenario(args.scenario))
    else:
        asyncio.run(run_part1_demo())


if __name__ == "__main__":
    main()
