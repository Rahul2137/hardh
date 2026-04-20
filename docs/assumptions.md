# Assumptions Document

## Part 1 — Parent Outreach Voice Agent

### Parent/Student Records
- **6 test parent records** created with varying levels of missing data
- Each parent has a unique `parent_id` (P001-P006)
- All parents have a known phone number (required for outreach)
- Fields that may be missing: email, mobile phone, school name, preferred weekday, preferred time, notes
- The `phone` field is the primary contact number used for voice calls
- `mobile_phone` is a separate field for alternate/mobile contact

### Known vs Missing Fields per Parent

| Parent | Known Fields | Missing Fields |
|--------|-------------|----------------|
| P001 (Sarah) | phone only | email, mobile, school, weekday, time, notes |
| P002 (Michael) | phone, school_name | email, mobile, weekday, time, notes |
| P003 (Priya) | phone, email | mobile, school, weekday, time, notes |
| P004 (James) | phone, mobile, school | email, weekday, time, notes |
| P005 (Lisa) | phone only | email, mobile, school, weekday, time, notes |
| P006 (David) | phone only | email, mobile, school, weekday, time, notes |

### Available Event Time Slots
- **Days**: Monday through Sunday
- **Hours**: 9:00 AM to 6:00 PM
- **Granularity**: Parents can suggest any time; we validate format only
- **Weekdays**: Monday through Sunday accepted; agent does not enforce weekday-only

### Outreach Rules
1. **One outreach attempt per parent per channel per day** (enforced via idempotency)
2. **Voice first, then WhatsApp** — WhatsApp is only sent if voice call goes unanswered
3. **Quiet hours**: 9 PM – 8 AM (no outreach attempts)
4. **Opt-out is immediate**: once a parent opts out, they are permanently suppressed
5. **Maximum 3 retry attempts** with exponential backoff
6. **Low confidence (< 0.5)** routes to human review automatically

### Validation Rules (Non-LLM)
- **Email**: Must match standard email format (RFC 5322 simplified)
- **Phone**: Must be 7-15 digits, may include +, -, (), spaces
- **Weekday**: Must be a valid day name (case-insensitive, partial matching supported)
- **Time**: Must match HH:MM AM/PM or 24h format, or informal ("morning", "afternoon")
- **School name**: 3-100 characters, no special characters (@, #, $, %)

### Target Google Sheet Structure

**Tab 1: "Parent Outreach"** (13 columns)
| Column | Description |
|--------|-------------|
| Parent ID | Unique identifier |
| Parent Name | Full name |
| Student Name | Child's name |
| Collected Fields | Semicolon-separated key=value pairs |
| Outreach Status | completed/partial/callback_requested/unreachable/wrong_number/opt_out/human_review |
| Confidence | 0.00 to 1.00 |
| Last Action Taken | What the system did |
| Next Recommended Action | What should happen next |
| Timestamp | ISO 8601 datetime |
| Attempt Number | Which attempt this was |
| Channel | voice/whatsapp |
| Validation Flags | Any validation warnings |
| Notes | Agent notes or conversation summary |

**Tab 2: "WhatsApp Log"** (6 columns)
| Column | Description |
|--------|-------------|
| Timestamp | When message was sent |
| Parent ID | Which parent |
| Parent Name | Parent's name |
| Phone | Phone number |
| Message | Full message text |
| Status | sent/failed/delivered |

**Tab 3: "Audit Log"** (5 columns)
| Column | Description |
|--------|-------------|
| Timestamp | When action occurred |
| Action | Action type |
| Parent ID | Related parent |
| Details | Action details |
| Status | Outcome |

### Confidence Scoring
- **0.95**: All data collected, no issues
- **0.85**: Special status detected (callback, opt-out, wrong number)
- **0.70**: Partial data collected, some fields missing
- **0.60**: Data collected but call ended abruptly
- **0.50**: No data extracted, status unclear
- **< 0.50**: Routed to human review

### Simulation Notes
- Voice calls are simulated with predefined conversation scripts
- WhatsApp messages are generated and logged but not actually sent
- In production, swap `VoiceSimulator` → `TwilioVoiceClient` and `WhatsAppSimulator` → `TwilioWhatsAppClient`
- The Google Sheets integration is real and production-ready (just needs credentials)
