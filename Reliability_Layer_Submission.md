# Reliability Layer Submission

## 1. Edge Cases & Failure Scenarios Defined
To ensure the parent outreach agent operates dependably in a production environment, we identified the following critical edge cases and failure scenarios:
1. **Duplicate Execution (Double Contact)**: The cron job or scheduler triggers twice due to a timeout, potentially annoying parents with duplicate calls or messages in a single day.
2. **Channel Unavailability (API Outages)**: The primary Voice API (e.g., Twilio/Bland) experiences an outage, returns a 5xx error, or the parent simply does not pick up.
3. **Data Hallucination / Poor Extraction**: The LLM misinterprets the parent's response, extracting incorrect information (e.g., extracting an invalid time format or hallucinating a phone number).
4. **Opt-Out Violations**: A parent explicitly requests to not be contacted, but the system inadvertently reaches out to them during the next batch process, risking compliance issues.
5. **Silent Failures**: A system component fails (e.g., Google Sheets quota limit reached) but the system doesn't register the failure, leading to lost data without any alerts.

## 2. Implemented Reliability Features
We have built a shared reliability module (`/reliability`) and integrated it into the orchestrator (`ParentOutreachAgent`). We implemented the following features:

1. **Idempotency** (`reliability/idempotency.py`)
   - **How it works**: Uses a local SQLite database to store hashes of `parent_id + channel + date`. 
   - **Impact**: Completely eliminates the risk of double-calling or double-messaging a parent on the same day, even if the execution pipeline is accidentally triggered multiple times.

2. **Audit Logs & Error Logging** (`reliability/audit.py`)
   - **How it works**: Every action (`voice_call_started`, `whatsapp_sent`, etc.) is logged to an `audit_log` SQLite table, complete with duration, metadata, and error details.
   - **Impact**: Provides full system traceability. In case of failure, operators can query the exact state and error payload, making it an alert-ready foundation.

3. **Confidence Thresholds & Human Review Queue** (`reliability/validation.py` & `ParentOutreachAgent`)
   - **How it works**: Data extracted by the LLM is passed through a deterministic rules engine (`DataValidator`). If validation flags are raised (e.g., invalid phone format, invalid time), the confidence score is mathematically adjusted downward.
   - **Impact**: If the confidence score drops below `LOW_CONFIDENCE_THRESHOLD`, the system safely assigns the result to a `HUMAN_REVIEW` status rather than blindly writing bad data.

4. **Opt-Out Suppression** (`ParentOutreachAgent.run_outreach`)
   - **How it works**: A strict pre-flight check validates the `opt_out` boolean on the parent record.
   - **Impact**: Immediate short-circuiting of the outreach workflow if a parent has opted out, ensuring compliance and preserving trust.

5. **Fallback Behavior** (`ParentOutreachAgent._handle_no_answer`)
   - **How it works**: If the primary communication channel (Voice) results in no answer after a threshold, the workflow gracefully catches this state and fails over to an asynchronous WhatsApp follow-up.
   - **Impact**: Maximizes reachability without requiring manual intervention when parents are busy.

---

## 3. Test Matrix

| Test ID | Feature | Scenario | Expected Outcome |
| :--- | :--- | :--- | :--- |
| **TR-01** | Idempotency | System triggers Voice call for `Parent A` twice on the same day. | First trigger succeeds. Second trigger is skipped; logs `outreach_skipped` status. |
| **TR-02** | Idempotency | System triggers WhatsApp for `Parent A` twice on the same day. | First message sends. Second attempt aborted before API call; status `UNREACHABLE`. |
| **TR-03** | Opt-Out | Parent record is flagged `opt_out = True`. Agent attempts to call. | Call aborted instantly. Result status marked `OPT_OUT`. Audit log records action. |
| **TR-04** | Fallback | Agent calls `Parent B`. Call rings but is unanswered. | Voice result marked no-answer. Agent automatically fires WhatsApp message template. |
| **TR-05** | Confidence | LLM extracts a phone number as `123`. | Validation catches invalid format. Confidence drops; status shifts to `HUMAN_REVIEW`. |
| **TR-06** | Confidence | LLM extracts a valid email and valid time. | Validation passes. Confidence remains high; status marked `COMPLETED` or `PARTIAL`. |
| **TR-07** | Audit | Agent runs standard successful flow. | `audit_log` records `voice_call_started`, `voice_call_completed`, and `sheets_write` with durations. |
| **TR-08** | Audit | Google Sheets API hits rate limit during write. | Error is caught, and `audit_log` records `sheets_write` with status `failed` and exact error trace. |
| **TR-09** | Validation | LLM extracts weekday as "someday". | Deterministic validation rejects it. Confidence penalty applied; flags appended to metadata. |
| **TR-10** | End-to-End | Parent opts-out during the voice call. | Call terminates. Status becomes `OPT_OUT`. Database prevents future outbound calls. |

---

## 4. Reducing Cost and Latency

**Reducing Cost:**
- **Model Routing**: Instead of using heavy models (like GPT-4 / Claude 3.5 Sonnet) for data extraction and classification, route simple conversation parsing to cheaper, faster models (like GPT-4o-mini or Claude 3 Haiku). 
- **Batching**: Group non-urgent WhatsApp messages and send them in scheduled batches to optimize API usage and reduce active compute time.
- **Prompt Caching**: If the platform supports prompt caching, caching the system instructions and school context will significantly drop token costs on subsequent calls.

**Reducing Latency:**
- **Streaming Responses**: During voice calls, stream the LLM generation directly to the Text-to-Speech (TTS) engine chunk by chunk to achieve sub-second conversational latency.
- **Asynchronous I/O**: Execute all third-party API calls (Google Sheets, Twilio, Database writes) asynchronously (`asyncio`). Move non-blocking tasks (like audit logging and Sheets writes) to background tasks so they don't block the core conversational loop.

---

## 5. Incident Note / Postmortem

**Incident:** Outbound Call Spam due to Retry Loop Failure
**Date:** Oct 12, 2026
**Duration:** 14 minutes

**Summary:**
At 09:00 AM, our automated outreach job began processing a batch of 200 parents. Due to an unhandled timeout from our primary Voice API provider, the job scheduler interpreted the batch as "failed" and retried the entire batch three times. Because idempotency checks were temporarily misconfigured to use local memory instead of the persistent SQLite DB, 45 parents received three consecutive phone calls within 14 minutes.

**Root Cause:**
1. Voice API provider experienced a latency spike, causing HTTP timeouts on our end.
2. The retry policy at the cron-job level lacked jitter and backoff, immediately retrying.
3. The Idempotency Manager had been mistakenly initialized with an in-memory DB path (`:memory:`) in the production environment variables, causing state to reset on every script restart.

**Resolution & Prevention:**
- **Immediate**: The scheduler was halted manually. An apology WhatsApp message was sent to affected parents.
- **Preventative Action 1**: Updated the deployment pipeline to strictly validate the `DB_PATH` environment variable, ensuring the idempotency layer always connects to persistent storage.
- **Preventative Action 2**: Implemented an exponential backoff with jitter on the external job scheduler.
- **Preventative Action 3**: Added an explicit API timeout catch block that logs an alert-ready field to the Audit DB, triggering a Slack notification to the engineering team upon high frequency of timeouts.
