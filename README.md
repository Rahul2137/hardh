# AI Outreach System — Architecture & Setup Guide

## Overview

An AI-powered outreach system that automates parent communications for collecting student information before group events. The system coordinates voice calls, WhatsApp follow-ups, data validation, and Google Sheets integration.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    CLI / FastAPI Server                   │
│                      (main.py)                           │
└────────────────────────┬────────────────────────────────┘
                         │
         ┌───────────────┼───────────────┐
         │                               │
┌────────▼────────┐            ┌────────▼────────┐
│  Part 1: Parent │            │  Part 2: Telegram│
│  Outreach Agent │            │  Student Agent   │
└────────┬────────┘            └────────┬─────────┘
         │                              │
    ┌────┴────┬─────────┐          ┌────┴────┐
    │         │         │          │         │
┌───▼──┐ ┌───▼──┐ ┌───▼───┐  ┌──▼───┐ ┌──▼───┐
│Voice │ │Whats │ │Google │  │Tele  │ │Google│
│Sim.  │ │App   │ │Sheets │  │gram  │ │Sheets│
│      │ │Sim.  │ │(REAL) │  │(REAL)│ │(REAL)│
└──────┘ └──────┘ └───────┘  └──────┘ └──────┘

         ┌──────────────────────────────┐
         │     Reliability Layer        │
         │  ┌─────────┐ ┌───────────┐  │
         │  │Idempot. │ │Validation │  │
         │  └─────────┘ └───────────┘  │
         │  ┌─────────┐ ┌───────────┐  │
         │  │Audit Log│ │Confidence │  │
         │  └─────────┘ └───────────┘  │
         └──────────────────────────────┘
```

## Tech Stack

| Component | Technology | Real/Simulated |
|-----------|-----------|----------------|
| Backend | Python 3.13 + FastAPI | Real |
| LLM | OpenAI GPT-4o | Simulated (with real-ready code) |
| Voice Calls | Voice Simulator | Simulated (Twilio/Vapi-ready) |
| WhatsApp | WhatsApp Simulator | Simulated (Twilio-ready) |
| Google Sheets | gspread + Service Account | **REAL** ✅ |
| Telegram Bot | python-telegram-bot | **REAL** ✅ |
| Database | SQLite (audit + idempotency) | Real |
| Validation | Regex-based (non-LLM) | Real |

## Setup

### Prerequisites
- Python 3.10+
- Google Cloud Service Account (for Sheets integration)
- Telegram Bot Token (for Part 2)
- OpenAI API Key (optional — runs in simulation without it)

### Installation

```bash
cd hardh
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your credentials
```

### Google Sheets Setup
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project and enable Google Sheets API
3. Create a Service Account and download the JSON key
4. Save as `credentials.json` in the project root
5. Create a new Google Sheet and share it with the service account email
6. Copy the Sheet ID from the URL and put it in `.env`

### Running

```bash
# Run all demo scenarios (simulation mode)
python main.py

# Run a specific scenario
python main.py --scenario 1

# Start as API server
python main.py --server

# Run in production mode (requires all API keys)
python main.py --production
```

## Project Structure

```
hardh/
├── main.py                  # Entry point (CLI + FastAPI)
├── config.py                # Configuration management
├── requirements.txt         # Python dependencies
├── .env                     # Environment variables
├── data/
│   └── parents.json         # Synthetic parent/student data
├── models/
│   └── parent.py            # Pydantic data models
├── agents/
│   ├── llm_client.py        # OpenAI wrapper + simulation
│   └── parent_outreach_agent.py  # Part 1 core agent
├── integrations/
│   ├── google_sheets.py     # Google Sheets client
│   ├── voice_simulator.py   # Voice call simulator
│   └── whatsapp_simulator.py # WhatsApp simulator
├── reliability/
│   ├── validation.py        # Non-LLM data validation
│   ├── audit.py             # SQLite audit logging
│   └── idempotency.py       # Duplicate prevention
├── outputs/                 # Generated output files
└── docs/                    # Documentation
```

## Assumptions

### Parent/Student Records
- Each parent has a known phone number (used for outreach)
- Some fields are pre-filled, others are missing and need collection
- Missing fields vary per parent record

### Available Event Time Slots
- Monday through Sunday
- 9:00 AM to 6:00 PM
- 30-minute increments

### Outreach Rules
- Maximum 3 attempts per parent per day
- No calls during quiet hours (9 PM - 8 AM)
- WhatsApp follow-up sent if voice call goes unanswered
- No duplicate outreach on the same day (idempotency)
- Opt-out is permanent and immediate

### Target Google Sheet Structure
Three tabs:
1. **Parent Outreach** — Main results with 13 columns
2. **WhatsApp Log** — All WhatsApp messages sent
3. **Audit Log** — System action audit trail

## Demo Scenarios

| # | Parent | Scenario | Expected Status |
|---|--------|----------|-----------------|
| 1 | Sarah Johnson | Answers, provides all info | `completed` (confidence: 0.95) |
| 2 | Michael Chen | No answer → WhatsApp | `unreachable` + WhatsApp sent |
| 3 | Priya Patel | Partial info, hangs up | `partial` (confidence: 0.60) |
| 4 | James Wilson | Callback requested | `callback_requested` |
| 5 | Lisa Rodriguez | Wrong number | `wrong_number` |
| 6 | David Kim | Opt-out | `opt_out` |

## API Endpoints (Server Mode)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Health check |
| POST | `/api/v1/outreach/run-all` | Run all scenarios |
| POST | `/api/v1/outreach/{parent_id}` | Run for specific parent |
| GET | `/api/v1/outreach/results` | Get all results |
| GET | `/api/v1/sheets/data` | Get simulated sheets data |
| GET | `/api/v1/audit/logs` | Get audit logs |
