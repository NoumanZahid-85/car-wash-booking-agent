# Car Wash Dual-Channel Booking Agent

Two conversational AI booking agents for a single-location car wash — one over WhatsApp (Baileys), one over voice (LiveKit + Uplift AI) — sharing one deterministic booking engine so a slot booked on either channel is instantly unavailable on the other. Built as a portfolio piece to demonstrate production-grade conversational AI architecture, not a tutorial toy.

## Problem & Success Criteria

**Problem:** Small service businesses like car washes lose bookings to phone tag and missed WhatsApp messages. A conversational agent that can hold a real conversation (not a rigid menu tree) and reliably reserve a slot — without double-booking — replaces that friction on two channels customers already use.

**Success criteria:**
- A real WhatsApp number, when messaged in plain English or Urdu ("I want to book a wash for Saturday"), collects name, vehicle type, date, time, and phone, and ends with a confirmed slot written to the database.
- Opening a browser link and talking out loud accomplishes the same booking, over voice, in English or Urdu.
- Booking the *same* slot from both channels at nearly the same time results in exactly one confirmed booking and one clear rejection — never two confirmations for one slot.
- `/admin/bookings` shows every booking made from either channel, live, in one place.
- The whole thing is reachable by a public URL, deployed on free infrastructure, with zero recurring cost.

**Explicit non-goals (v1):**
- No payments/deposits.
- No multi-location, multi-service-type, or variable-duration bookings — one location, one service, fixed slot grid.
- No real PSTN phone calling (no dial-in number) — voice channel is a browser/WebRTC call, by design (see Alternatives Considered).
- No cancellation/reschedule flows — booking only. (Natural Phase 12 if you extend this later.)
- No authentication on the admin page — v1 assumes you're the only one hitting it. Flagged in the README as a known gap, not silently ignored.

## Constraints (locked in during grilling)

| Constraint | Value |
|---|---|
| Scale/users | Portfolio demo — single location, low concurrent traffic, but must survive two near-simultaneous bookings correctly |
| Deployment target | Render (2 free web services) + Supabase (free Postgres) — publicly reachable, not just local |
| Budget | $0 — every tool chosen must have a genuinely free tier, no trial-then-paywall |
| Timeline | Open — optimize for clarity and small verifiable steps over speed |
| Team | Solo, first-year-programmer-readable — every phase must be small enough to fully understand before moving on |
| Data/compliance | Customer name, phone number, vehicle type stored — no payment data, no special regulatory regime, but treat phone numbers as data worth not leaking (no public API that dumps all customer phone numbers unauthenticated) |
| Languages | English and Urdu, config-driven (not hardcoded per-agent) |
| Repo shape | One GitHub repo: `whatsapp-agent/`, `voice-agent/`, `shared/` |

## Research Summary

- **Unofficial WhatsApp clients (Baileys) carry real ban risk.** They speak WhatsApp Web's protocol without Meta's blessing; WhatsApp's anti-abuse systems look for exactly this pattern. Mitigation: use a dedicated spare number, never your personal one, and keep message rates human-paced.
- **The dominant 2026 failure mode in LLM-driven booking/voice agents is letting the model itself decide state transitions.** Real postmortems (a patient's slot double-booked because the LLM said "confirmed" while the DB said "taken"; a voice exam agent that corrupted its own message history with hallucinated tool calls) converge on the same fix: **the LLM only handles language — a deterministic code layer (state machine + DB transaction) owns the actual state change.** This plan bakes that in from Phase 1, not bolted on later.
- **Uplift AI's own reference architecture is LiveKit Agents (Python) + their STT/TTS plugin + an LLM**, demoed over a browser/WebRTC link, not a phone line. That's the free, officially-supported path — real PSTN calling needs a paid telephony leg (Twilio/Telnyx) regardless of which voice AI sits underneath.
- **Meta's official WhatsApp Cloud API is actually free for customer-initiated conversations** — but was ruled out here in favor of Baileys per your explicit choice; noted as the safer alternative in Alternatives Considered in case the number gets flagged later.
- **Free-tier landscape that's actually free-forever (not free-trial-then-paywall):** Supabase (hosted Postgres, pauses after 7 days total inactivity, data survives), Render (web services, cold-starts after 15 min idle), Groq (fast LLM inference, function-calling capable, no card required).
- **Race conditions in slot booking are a solved problem at the database level** — a `SELECT ... FOR UPDATE` row lock or a unique constraint on `(slot_id)` in the bookings table, inside a single transaction, is enough for this scale. No need for a distributed lock service.

## Tech Stack Decision

**Database:** Supabase (hosted Postgres)
- Rejected: SQLite file — can't be shared between two separately-deployed Render services (WhatsApp bot and voice/API service), no free managed hosting.
- Rejected: Render's own free Postgres — Render's free Postgres tier has historically been time-limited/expiring; Supabase's free tier is pause-on-inactivity but doesn't expire and gives you a dashboard/table editor for free, which doubles as a manual admin tool during development.
- Why this one: real SQL with transactions/row-locking (needed for the no-double-booking guarantee), free forever, reachable from both Node and Python over a normal Postgres connection string.

**Booking engine + Booking API:** Python + FastAPI
- Rejected: writing the engine twice (once in Node for the WhatsApp bot, once in Python for the voice agent) — duplicated logic is exactly how double-booking bugs sneak in; the whole point of this architecture is one source of truth.
- Rejected: Node/Express for the shared API — would work equally well, but Python was chosen because the voice agent (LiveKit Agents) is Python-native anyway, so the API and the voice agent can share code/models directly without a network hop for at least one of the two clients.
- Why this one: FastAPI gives typed request/response models (Pydantic) for free, which is exactly the "typed tools cut malformed calls" pattern research surfaced as standard practice for 2026 agent tool-calling.

**WhatsApp channel:** Node.js + TypeScript + Baileys
- Rejected: whatsapp-web.js — heavier (drives a real headless Chromium), more RAM, slower to deploy on a free-tier box.
- Rejected: Meta's official Cloud API — genuinely free too and safer (no ban risk), but you explicitly want Baileys for this build; documented here so future-you can switch if the number gets flagged (see Alternatives Considered).
- Why this one: Baileys speaks the WhatsApp multi-device protocol directly (no browser needed), is the most common free/open-source choice for this exact use case, and matches your Node comfort.

**Voice channel:** Python + LiveKit Agents + Uplift AI (STT/TTS)
- Rejected: rolling your own WebRTC signaling/media server — LiveKit is open-source, free self-hostable or free-tier cloud, and is literally what Uplift AI's own docs use — no reason to reinvent it.
- Why this one: matches Uplift's official integration path exactly, and LiveKit's `AgentSession` already handles VAD (voice activity detection), turn-taking, and interruption — all things that are painful to hand-build correctly.

**LLM (both agents):** Groq (Llama 3.3 70B), free tier
- Rejected: OpenAI/Anthropic — no meaningful permanent free tier, would violate the $0 constraint the moment you started real testing.
- Rejected: Gemini free tier — more daily requests, but noticeably higher latency than Groq; latency matters more for the voice channel (turn-taking feels broken above ~1s), and using the same provider for both channels keeps the prompt/tool-calling code identical instead of forked.
- Why this one: sub-second responses (LPU hardware), free tier with no card required, supports function calling well enough for a small, fixed tool set (get_available_slots, hold_slot, confirm_booking).

**Deployment:** Render (2 free web services: `booking-api` [Python/FastAPI, also serves admin page and hosts the voice agent's LiveKit worker process] and `whatsapp-bot` [Node/Baileys])
- Rejected: Railway/Fly.io — neither has a real permanent free tier anymore as of 2026 (Railway: one-time trial credit only; Fly.io: 2-hour trial only).
- Why this one: only platform researched with an actually-permanent $0 tier; the tradeoff (cold start after 15 min idle) is acceptable for a portfolio demo and is explicitly disclosed in the README rather than hidden.

## Architecture Overview

```
                    ┌─────────────────────────┐
                    │   Supabase (Postgres)   │
                    │  slots | bookings tables │
                    └────────────▲─────────────┘
                                 │  SQL (transactions)
                                 │
                    ┌────────────┴─────────────┐
                    │   booking-api (FastAPI)   │
                    │  - booking engine (core)  │
                    │  - /slots  /hold /confirm │
                    │  - /admin/bookings (HTML) │
                    └───▲──────────────────▲────┘
                        │ HTTP                │ (same process,
                        │                     │  direct call)
        ┌───────────────┴──────┐    ┌─────────┴────────────┐
        │   whatsapp-bot        │    │   voice-agent worker  │
        │   Node.js + Baileys   │    │   Python + LiveKit +   │
        │   + Groq (tool-calling)│   │   Uplift AI + Groq     │
        └───────────▲───────────┘    └──────────▲─────────────┘
                    │                            │
              WhatsApp user                LiveKit room (browser link)
```

Both agents are conversation front-ends only. Neither one ever writes "booked" to the database directly — they call the Booking API, which owns the transaction and returns either a confirmation or a "someone just took that slot, try another" rejection. This is the deterministic-core-around-a-probabilistic-model pattern from the research summary, applied literally.

## Phases

Each phase depends ONLY on earlier phases. Each is independently runnable and testable. Phases are intentionally small — each should take a first-year programmer an afternoon, not a week.

---

### Phase 1: Repo scaffold + Supabase project

**Depends on:** None (starting point)
**Goal:** An empty but correctly-structured repo, and a live (empty) Supabase Postgres database you can connect to from your laptop.

**Tasks:**
1. Create the GitHub repo with this layout:
   ```
   /shared/          (SQL schema + seed scripts live here)
   /booking-api/      (Python/FastAPI — Phase 3 onward)
   /whatsapp-agent/   (Node/TypeScript — Phase 6 onward)
   /voice-agent/      (Python/LiveKit — Phase 8 onward)
   README.md
   .gitignore         (must ignore .env, node_modules/, __pycache__/, venv/)
   ```
2. Create a Supabase project (you said you already have an account — just create a new project for this one).
3. Copy the Postgres connection string from Supabase's dashboard into a local `.env` file (never committed) as `DATABASE_URL`.
4. Confirm you can connect to it with any Postgres client (Supabase's own SQL editor in the browser is enough for this phase — no code needed yet).

**Definition of Done (verify before moving on):**
1. Run: open the Supabase dashboard → SQL Editor → run `select 1;`
2. Expected result: returns `1` with no error — confirms the project is live and reachable.
3. If it doesn't match: check the project isn't still "provisioning" (takes ~2 minutes after creation on Supabase's side). Wait and retry.

**Watch out for:** Committing `.env` by accident — set up `.gitignore` in this phase, before any secret ever exists in the repo's history, not after.

---

### Phase 2: Database schema + fixed slot grid

**Depends on:** Phase 1
**Goal:** Two real tables in Supabase — `slots` (the fixed daily grid) and `bookings` — with the one constraint that makes double-booking structurally impossible, plus a seed script that fills a week of slots.

**Tasks:**
1. Write `shared/schema.sql` defining `slots` and `bookings`.
2. Run it against Supabase (SQL Editor, or `psql $DATABASE_URL -f shared/schema.sql`).
3. Write `shared/seed_slots.py` — a small script that inserts one week of slots for a single car wash bay, e.g. 9am–6pm, hourly, Mon–Sat.
4. Run the seed script and confirm rows appear in Supabase's table editor.

**Starter code:**

```sql
-- shared/schema.sql

-- Why this design: `slots` are pre-generated (not computed on the fly) so that
-- "hold a slot" is a single UPDATE on an existing row, not a race to invent one.
-- The UNIQUE constraint on bookings.slot_id is the actual double-booking guard —
-- even if application code has a bug, Postgres itself will refuse a second
-- confirmed booking on the same slot.

CREATE TABLE slots (
    id SERIAL PRIMARY KEY,
    slot_date DATE NOT NULL,
    slot_time TIME NOT NULL,
    status TEXT NOT NULL DEFAULT 'available', -- 'available' | 'held' | 'booked'
    held_at TIMESTAMPTZ,                       -- TODO: think about why we need to expire stale holds
    UNIQUE (slot_date, slot_time)
);

CREATE TABLE bookings (
    id SERIAL PRIMARY KEY,
    -- TODO: add slot_id INTEGER REFERENCES slots(id), with a UNIQUE constraint
    -- Hint: a UNIQUE constraint on slot_id means Postgres itself rejects a
    -- second row pointing at an already-booked slot — this is your real
    -- double-booking guard, not application code.
    customer_name TEXT NOT NULL,
    phone_number TEXT NOT NULL,
    vehicle_type TEXT NOT NULL,
    channel TEXT NOT NULL, -- 'whatsapp' | 'voice'
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

```python
# shared/seed_slots.py
import os
import psycopg2
from datetime import date, time, timedelta

# Why a separate seed script: slots are business config (business hours),
# not something the app should compute live — this keeps that config in one
# obvious place both agents' capacity depends on.

DATABASE_URL = os.environ["DATABASE_URL"]

def generate_week_of_slots(start_date: date):
    # TODO: for each of 6 days (Mon-Sat) starting at start_date,
    # for each hour from 9 to 17 (9am-5pm start times, last wash ends 6pm),
    # yield (that_date, that_time)
    # Hint: use datetime.time(hour=h) and date + timedelta(days=d)
    pass

def main():
    conn = psycopg2.connect(DATABASE_URL)
    # TODO: for each (slot_date, slot_time) from generate_week_of_slots(date.today()),
    # INSERT INTO slots (slot_date, slot_time) VALUES (%s, %s)
    # ON CONFLICT (slot_date, slot_time) DO NOTHING  -- Learn: why ON CONFLICT DO NOTHING matters if you rerun this script
    conn.commit()

if __name__ == "__main__":
    main()
```

**Definition of Done (verify before moving on):**
1. Run: `python shared/seed_slots.py` then check Supabase's table editor for the `slots` table.
2. Expected result: roughly 54 rows (6 days × 9 hourly slots), all `status = 'available'`.
3. If it doesn't match: check `DATABASE_URL` is loaded (print it, minus the password, to confirm it's not empty) and that `schema.sql` actually ran first.

**Watch out for:** Running the seed script twice without the `ON CONFLICT` clause — you'd get a duplicate-key error, which is actually the *correct* behavior to understand here, not a bug to silently swallow.

---

### Phase 3: Booking engine core functions

**Depends on:** Phase 2
**Goal:** Plain Python functions — `get_available_slots`, `hold_slot`, `confirm_booking` — that are the *only* code in the whole project allowed to change a slot's status, proven correct with a real concurrency test (two "simultaneous" attempts to book the same slot).

**Tasks:**
1. Write `booking-api/engine.py` with the three functions.
2. `hold_slot` and `confirm_booking` must run inside a single DB transaction using `SELECT ... FOR UPDATE` so two near-simultaneous callers can't both succeed.
3. Write `booking-api/test_engine.py` that literally fires two `hold_slot` calls at the same slot (using threads or `asyncio.gather`) and asserts exactly one succeeds.
4. Run the test and watch it actually fail first if you comment out the row lock — then pass once it's in — so you've *proven* the guard works, not assumed it.

**Starter code:**

```python
# booking-api/engine.py
import psycopg2
from datetime import datetime, timedelta

# Why FOR UPDATE: without it, two connections can both read status='available'
# before either writes, and both proceed to book — the classic
# read-then-write race condition. FOR UPDATE makes the second connection
# wait for the first transaction to finish before it's even allowed to read.
# This is the deterministic core the whole project's safety depends on.

def get_available_slots(conn, slot_date):
    # TODO: SELECT id, slot_time FROM slots WHERE slot_date = %s AND status = 'available' ORDER BY slot_time
    # Hint: this one doesn't need FOR UPDATE — it's read-only, no state change
    pass

def hold_slot(conn, slot_id: int) -> bool:
    """Temporarily reserve a slot while the conversation collects the rest
    of the customer's details. Returns False if it was already taken."""
    with conn:
        with conn.cursor() as cur:
            # TODO: SELECT status FROM slots WHERE id = %s FOR UPDATE
            # then, only if status == 'available':
            #   UPDATE slots SET status = 'held', held_at = now() WHERE id = %s
            # return True if you updated it, False if it wasn't available
            # Learn: psycopg2's `with conn:` block auto-commits on success,
            # auto-rolls-back on exception — that's what makes this atomic
            pass

def confirm_booking(conn, slot_id: int, customer_name: str, phone_number: str, vehicle_type: str, channel: str) -> bool:
    """Turn a held slot into a real booking. Returns False if the slot
    wasn't in 'held' state (expired, or never held)."""
    # TODO: similar FOR UPDATE pattern, then:
    #   UPDATE slots SET status = 'booked' WHERE id = %s
    #   INSERT INTO bookings (slot_id, customer_name, phone_number, vehicle_type, channel) VALUES (...)
    pass
```

**Definition of Done (verify before moving on):**
1. Run: `pytest booking-api/test_engine.py -v`
2. Expected result: a test named something like `test_concurrent_hold_only_one_wins` passes, and prints/asserts that exactly 1 of 2 simultaneous `hold_slot` calls returned `True`.
3. If it doesn't match: temporarily remove the `FOR UPDATE` clause and rerun — you should now see the test fail (both calls succeed). Put it back, confirm it passes again. This before/after is the actual proof the guard works, not a guess.

**Watch out for:** Testing this with sequential calls instead of genuinely concurrent ones — a race condition bug won't show up if your "test" never actually races.

---

### Phase 4: Booking API (FastAPI)

**Depends on:** Phase 3
**Goal:** The engine from Phase 3, reachable over HTTP, so any client (WhatsApp bot, voice agent, or just `curl`) can list slots, hold one, and confirm a booking — without needing direct DB access or Python imports.

**Tasks:**
1. Write `booking-api/main.py` — a FastAPI app with `GET /slots?date=...`, `POST /hold`, `POST /confirm`.
2. Define Pydantic request/response models for each — this is the "typed tools" pattern that prevents malformed calls from the LLM layer later.
3. Wire each endpoint to the Phase 3 engine functions.
4. Run it locally (`uvicorn booking-api.main:app --reload`) and exercise all three endpoints with `curl`.

**Starter code:**

```python
# booking-api/main.py
from fastapi import FastAPI
from pydantic import BaseModel
from datetime import date
import psycopg2, os
from engine import get_available_slots, hold_slot, confirm_booking

# Why typed Pydantic models: when Groq's function-calling generates a tool
# call, FastAPI will reject a malformed one (wrong types, missing fields)
# with a clear 422 error instead of your engine code getting garbage input
# and doing something undefined. This is where "typed tools cut malformed
# calls" from the research becomes real, not just a design slogan.

app = FastAPI()

def get_conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])

class HoldRequest(BaseModel):
    slot_id: int

class ConfirmRequest(BaseModel):
    slot_id: int
    customer_name: str
    phone_number: str
    vehicle_type: str
    channel: str  # 'whatsapp' or 'voice'

@app.get("/slots")
def list_slots(slot_date: date):
    # TODO: open a connection, call get_available_slots, return as JSON
    # Hint: FastAPI auto-serializes lists of dicts/tuples — shape the return
    # value as a list of {"id": ..., "time": ...}
    pass

@app.post("/hold")
def hold(req: HoldRequest):
    # TODO: call hold_slot, return {"success": True/False}
    pass

@app.post("/confirm")
def confirm(req: ConfirmRequest):
    # TODO: call confirm_booking, return {"success": True/False}
    pass
```

**Definition of Done (verify before moving on):**
1. Run: `curl "http://localhost:8000/slots?slot_date=2026-08-10"`
2. Expected result: a JSON array of available slot objects for that date (from Phase 2's seed data).
3. Then run: `curl -X POST http://localhost:8000/hold -H "Content-Type: application/json" -d '{"slot_id": 1}'` twice in a row.
4. Expected result: first call returns `{"success": true}`, second call (same slot_id) returns `{"success": false}` — proving the API surfaces the Phase 3 guarantee correctly, not just the raw function does.

**Watch out for:** Opening a new DB connection per request without closing it — under load this exhausts Supabase's free-tier connection limit. A simple context manager (`with get_conn() as conn:`) per request is enough at this scale; don't over-engineer a connection pool yet.

---

### Phase 5: Admin bookings page

**Depends on:** Phase 4
**Goal:** A plain HTML page at `/admin/bookings` on the same FastAPI app, listing every booking from every channel — the "store or display" requirement, made genuinely useful rather than a bare console log.

**Tasks:**
1. Add a `GET /admin/bookings` route to `booking-api/main.py` that queries the `bookings` table joined with `slots` (to show date/time, not just IDs).
2. Return it as server-rendered HTML (a simple `<table>` — no JS framework needed) using FastAPI's `HTMLResponse`.
3. Manually insert a test booking (via `/confirm` from Phase 4) and confirm it shows up.

**Starter code:**

```python
# booking-api/main.py (addition)
from fastapi.responses import HTMLResponse

# Why plain server-rendered HTML: this is an internal tool for you, the
# business owner — a React app here would be complexity with no payoff.
# Production teams reach for "boring" server-rendered admin pages for
# exactly this kind of internal, low-traffic view all the time.

@app.get("/admin/bookings", response_class=HTMLResponse)
def admin_bookings():
    # TODO: SELECT b.*, s.slot_date, s.slot_time FROM bookings b
    #       JOIN slots s ON b.slot_id = s.id ORDER BY s.slot_date, s.slot_time
    # Hint: build an HTML string with an f-string loop over rows —
    # a <table> with one <tr> per booking is enough for v1
    rows_html = ""  # TODO: build this
    return f"<html><body><h1>Bookings</h1><table>{rows_html}</table></body></html>"
```

**Definition of Done (verify before moving on):**
1. Run: `curl -X POST http://localhost:8000/confirm -H "Content-Type: application/json" -d '{"slot_id": 2, "customer_name": "Test User", "phone_number": "0300...", "vehicle_type": "Sedan", "channel": "whatsapp"}'` then open `http://localhost:8000/admin/bookings` in a browser.
2. Expected result: a visible HTML table row with "Test User", the right date/time, "Sedan", "whatsapp".
3. If it doesn't match: check the JOIN condition matches your actual column names from Phase 2's schema.

**Watch out for:** This page has no authentication (documented as a known v1 gap) — don't put anything more sensitive than name/phone/vehicle in it, and don't link it publicly outside your own testing.

---

### Phase 6: WhatsApp connection (Baileys, no AI yet)

**Depends on:** Phase 1 (repo scaffold only — this phase doesn't need the booking engine yet)
**Goal:** A running Node process that logs into WhatsApp via Baileys (QR code scan) and echoes back any message it receives — proves the channel itself works before adding any AI complexity on top.

**Tasks:**
1. `cd whatsapp-agent && npm init -y && npm install @whiskeysockets/baileys qrcode-terminal`
2. Write `whatsapp-agent/src/index.ts` that connects, prints a QR code to the terminal, and on any incoming text message replies `"echo: <message>"`.
3. Scan the QR with your spare WhatsApp number's app.
4. Send it a message from another phone and confirm the echo comes back.

**Starter code:**

```typescript
// whatsapp-agent/src/index.ts
import makeWASocket, { useMultiFileAuthState, DisconnectReason } from '@whiskeysockets/baileys'
import qrcode from 'qrcode-terminal'

// Why useMultiFileAuthState: it persists the login session to disk so you
// don't have to re-scan the QR code every time you restart the process —
// important once this is a long-running deployed service, not just a script.

async function startBot() {
  const { state, saveCreds } = await useMultiFileAuthState('whatsapp-agent/auth_state')
  const sock = makeWASocket({ auth: state })

  sock.ev.on('creds.update', saveCreds)

  sock.ev.on('connection.update', (update) => {
    const { qr, connection } = update
    if (qr) qrcode.generate(qr, { small: true })
    // TODO: on connection === 'close', check if you should reconnect
    // Hint: DisconnectReason.loggedOut means don't reconnect (need new QR);
    // anything else, call startBot() again
  })

  sock.ev.on('messages.upsert', async ({ messages }) => {
    // TODO: for each message in messages, if it has real text content
    // and isn't from yourself (msg.key.fromMe), reply with "echo: <text>"
    // Hint: sock.sendMessage(remoteJid, { text: "..." })
    // Learn: message text lives at msg.message?.conversation or
    // msg.message?.extendedTextMessage?.text depending on client
  })
}

startBot()
```

**Definition of Done (verify before moving on):**
1. Run: `npx tsx whatsapp-agent/src/index.ts`, scan the printed QR with your spare number.
2. Send "hello" to that number from a different phone.
3. Expected result: you receive "echo: hello" back within a couple seconds.
4. If it doesn't match: check the terminal for connection errors first — a stale `auth_state` folder from a failed previous attempt is the most common cause; delete it and rescan.

**Watch out for:** Baileys sessions can get logged out if WhatsApp detects unusual activity — keep message volume low and human-paced while testing, per the research on unofficial-client ban risk.

---

### Phase 7: WhatsApp conversation + booking tool calls

**Depends on:** Phase 6, Phase 4
**Goal:** Replace the echo with a real Groq-powered conversation that collects name, vehicle type, date, time, phone, and books it by calling the Phase 4 API — the full WhatsApp agent.

**Tasks:**
1. `npm install groq-sdk axios`
2. Define three "tools" for Groq's function-calling matching the Booking API: `list_available_slots`, `hold_slot`, `confirm_booking`.
3. Maintain a per-conversation message history (in-memory `Map<phoneNumber, messages[]>` is fine for v1 — document that it resets on restart).
4. On each incoming message: append to history, call Groq with the tools defined, and when Groq requests a tool call, actually call the Booking API (Phase 4) over HTTP — never let Groq's text output alone be treated as a confirmed booking.
5. Write the system prompt to include both English and Urdu instructions, and instruct the model to reply in whichever language the customer used.

**Starter code:**

```typescript
// whatsapp-agent/src/agent.ts
import Groq from 'groq-sdk'
import axios from 'axios'

const groq = new Groq({ apiKey: process.env.GROQ_API_KEY })
const BOOKING_API = process.env.BOOKING_API_URL // e.g. http://localhost:8000

// Why tools mirror the Booking API 1:1: the LLM should never have a tool
// that does more than the API allows — if hold_slot the tool can only ever
// call POST /hold, there's no path for the model to "decide" a booking is
// confirmed without the deterministic engine's row lock actually agreeing.

const tools = [
  {
    type: 'function' as const,
    function: {
      name: 'list_available_slots',
      description: 'Get available car wash slots for a given date',
      parameters: {
        type: 'object',
        properties: { date: { type: 'string', description: 'YYYY-MM-DD' } },
        required: ['date'],
      },
    },
  },
  // TODO: define hold_slot(slot_id) and confirm_booking(slot_id, customer_name,
  // phone_number, vehicle_type) the same way, matching Phase 4's Pydantic models exactly
]

const SYSTEM_PROMPT = `You are a booking assistant for a car wash.
Reply in the same language the customer uses — English or Urdu.
Collect: customer name, vehicle type, preferred date, preferred time, phone number.
Never tell the customer their booking is confirmed until the confirm_booking tool
call actually returns success: true. If it returns false, say the slot was just
taken and offer to check availability again.`

const conversations = new Map<string, any[]>()

export async function handleMessage(phoneNumber: string, text: string): Promise<string> {
  // TODO:
  // 1. get or create this phone number's message history, seeded with SYSTEM_PROMPT
  // 2. push the new user message
  // 3. call groq.chat.completions.create with { messages, tools }
  // 4. if the response includes tool_calls, for each one:
  //    - actually call BOOKING_API via axios (GET /slots, POST /hold, POST /confirm)
  //    - push the tool result back into the message history
  //    - call groq again so it can respond to the tool result in natural language
  // 5. return the final assistant text to send back to the customer
  // Hint: this loop (call model -> handle tool calls -> call model again) is
  // the standard function-calling pattern — Groq's docs show the exact shape
  return "TODO"
}
```

**Definition of Done (verify before moving on):**
1. Run the bot (with `booking-api` also running locally) and message it: "I want to book a wash on Saturday around 2pm, I have a Honda Civic."
2. Expected result: the bot asks for any missing details (name, phone if not inferable), then confirms with an actual date/time it pulled from real availability — and `/admin/bookings` (Phase 5) shows the new row.
3. Try booking the exact same slot again in a second conversation before finishing the first — expected: the second one gets a "just taken" response, not a false confirmation.
4. If it doesn't match: log the raw Groq tool_calls response first — the most common bug is malformed JSON arguments the API's Pydantic models correctly reject; read the 422 error body to see what's wrong.

**Watch out for:** Trusting the model's own text ("Great, you're booked!") without checking the actual tool result — this is the exact bug the whole architecture exists to prevent. Grep your own code for any place you send a confirmation message without a preceding successful `confirm_booking` tool result.

---

### Phase 8: Voice agent connection (LiveKit + Uplift AI, no booking logic yet)

**Depends on:** Phase 1
**Goal:** Open a browser link, speak, and hear Uplift AI's voice say something back — proves the voice pipeline (mic → STT → LLM → TTS → speaker) works before wiring in booking logic.

**Tasks:**
1. `cd voice-agent && python -m venv venv && source venv/bin/activate`
2. `pip install "livekit-agents" "livekit-plugins-openai" "livekit-plugins-upliftai" "livekit-plugins-silero"`
3. Sign up for LiveKit Cloud's free tier (or self-host) to get room credentials.
4. Write `voice-agent/src/main.py` — a minimal `AgentSession` using Uplift AI TTS and any STT, with a trivial "you are a friendly car wash receptionist, just chat" prompt — no tools yet.
5. Open LiveKit's web playground, join the room, and talk to it.

**Starter code:**

```python
# voice-agent/src/main.py
from livekit import agents
from livekit.agents import AgentSession, Agent
from livekit.plugins import openai, upliftai, silero

# Why this shape mirrors Uplift AI's own docs exactly: it's the officially
# supported integration path — VAD (voice activity detection), turn-taking,
# and barge-in (interrupting the agent mid-sentence) are handled by LiveKit's
# AgentSession, not code you'd want to hand-roll correctly on a first pass.

class ReceptionistAgent(Agent):
    def __init__(self):
        super().__init__(instructions="""You are a friendly car wash receptionist.
        For now, just have a normal conversation — no booking logic yet.
        Reply in whichever language the customer speaks: English or Urdu.""")

async def entrypoint(ctx: agents.JobContext):
    session = AgentSession(
        stt=openai.STT(model="gpt-4o-transcribe"),  # TODO: confirm language param for Urdu support
        llm=openai.LLM(model="llama-3.3-70b-versatile", base_url="https://api.groq.com/openai/v1"),
        # ^ Hint: Groq exposes an OpenAI-compatible endpoint, so the openai.LLM
        # plugin works against it if you point base_url + api_key at Groq
        tts=upliftai.TTS(voice_id="v_meklc281"),  # TODO: pick an appropriate voice from Uplift's catalog
        vad=silero.VAD.load(),
    )
    await session.start(room=ctx.room, agent=ReceptionistAgent())

if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
```

**Definition of Done (verify before moving on):**
1. Run: `python voice-agent/src/main.py dev`, open LiveKit's web playground pointed at your room.
2. Say "Hello, how are you?" out loud.
3. Expected result: you hear a spoken reply within a couple seconds, in a natural voice, on-topic.
4. If it doesn't match: check each plugin's API key env var is set (`GROQ_API_KEY`, `UPLIFTAI_API_KEY`, and whatever LiveKit itself needs) — a missing key here fails silently as "no response" far more often than a loud error.

**Watch out for:** STT language mismatches — if Uplift's TTS is set to speak Urdu but STT is set to transcribe English-only, the model will "hear" garbage. Confirm both are configured for the same set of languages before debugging conversation logic.

---

### Phase 9: Voice conversation + booking tool calls

**Depends on:** Phase 8, Phase 4
**Goal:** The same three booking tools from Phase 7, now callable by the voice agent — a full spoken booking conversation, ending in a real row in `/admin/bookings`.

**Tasks:**
1. Add the same `list_available_slots` / `hold_slot` / `confirm_booking` tool definitions to the LiveKit `Agent`, using LiveKit's function-tool decorator pattern.
2. Each tool function makes the same HTTP calls to the Phase 4 Booking API that the WhatsApp agent makes — same API, same guarantees, proving the "one shared source of truth" goal from the grill.
3. Update the system prompt with the same "never claim booked until confirm_booking returns success" rule as Phase 7's WhatsApp prompt — copy the wording deliberately, don't let the two prompts drift into different behavior.
4. Test a full spoken booking end to end.

**Starter code:**

```python
# voice-agent/src/agent.py
from livekit.agents import function_tool, RunContext
import httpx

BOOKING_API = "http://localhost:8000"  # TODO: load from env var for deployment

# Why @function_tool: LiveKit's decorator generates the tool schema the LLM
# sees automatically from the Python type hints — same "typed tool" safety
# net as the FastAPI Pydantic models on the other end of this HTTP call.

@function_tool
async def list_available_slots(context: RunContext, date: str) -> str:
    """Get available car wash slots for a given date (YYYY-MM-DD)."""
    async with httpx.AsyncClient() as client:
        # TODO: GET f"{BOOKING_API}/slots?slot_date={date}", return a readable
        # string summary the LLM can speak from, e.g. "10am, 11am, 2pm available"
        pass

@function_tool
async def hold_slot(context: RunContext, slot_id: int) -> str:
    # TODO: POST to /hold, same pattern as the WhatsApp agent's Phase 7 tool
    pass

@function_tool
async def confirm_booking(context: RunContext, slot_id: int, customer_name: str, phone_number: str, vehicle_type: str) -> str:
    # TODO: POST to /confirm with channel="voice", same pattern as Phase 7
    # Remember: only say "booked" in your return string if success is True
    pass
```

**Definition of Done (verify before moving on):**
1. Run the voice agent (with `booking-api` running) and speak a full booking request out loud, including all required details across a couple of turns.
2. Expected result: the agent speaks a real confirmation quoting an actual date/time, and `/admin/bookings` shows the new row with `channel = 'voice'`.
3. Then, in a WhatsApp conversation (Phase 7), try to book that exact same now-taken slot.
4. Expected result: the WhatsApp agent reports it's unavailable — proving the two channels genuinely share one source of truth, not two separate databases that happen to look similar.

**Watch out for:** LiveKit's function-tool calling running inside the low-latency voice loop — a slow Booking API call (e.g. a cold Render instance, see Phase 10) can make the agent go silent mid-conversation. Log tool call durations here so you notice this before deployment, not after.

---

### Phase 10: Deployment (Render + Supabase, wired together)

**Depends on:** Phase 5, Phase 7, Phase 9
**Goal:** Both services running on Render, pointed at the same production Supabase database, publicly reachable — the "actually deployed" requirement.

**Tasks:**
1. On Render, create a Web Service for `booking-api/` (Python), with `DATABASE_URL` and `GROQ_API_KEY` set as environment variables (never committed to the repo).
2. On Render, create a second Web Service for `whatsapp-agent/` (Node), with `GROQ_API_KEY` and `BOOKING_API_URL` (pointing at the first service's Render URL) as env vars.
3. Decide where the voice agent worker process runs — either as a third small Render service or triggered by LiveKit Cloud directly; document whichever you choose in the README along with why.
4. Re-scan the WhatsApp QR code once, against the deployed instance (Baileys' `auth_state` needs to persist across Render restarts — check whether Render's free tier gives you persistent disk, or store the auth state in Supabase/Render's key-value options instead).
5. Confirm all three pieces (WhatsApp, voice, admin page) work against the live, public Supabase database.

**Definition of Done (verify before moving on):**
1. From a phone with no code running locally, message the deployed WhatsApp number and complete a booking.
2. Open the deployed voice agent's public link from a different device and complete a booking.
3. Open `https://<your-booking-api>.onrender.com/admin/bookings` and see both bookings.
4. If it doesn't match: check Render's logs for each service first — most first-deploy failures are a missing environment variable, not a code bug.

**Watch out for:** Render's free tier cold-starting after 15 minutes idle — the first WhatsApp message or voice connection after a quiet period will be slow (~30-50s). This is expected, not a bug; document it plainly in the README rather than trying to silently work around it (e.g., don't build a fake keep-alive ping — that's dishonest about what "free tier" actually means).

---

### Phase 11: README + submission packaging

**Depends on:** Phase 10
**Goal:** A README that lets a stranger (or a reviewer) understand, run, and evaluate the whole project without needing you in the room to explain it.

**Tasks:**
1. Write `README.md` covering: what this is, the architecture diagram (reuse the one from this plan), how the "no double-booking" guarantee actually works (this is the most impressive/differentiating part — don't bury it), setup instructions for both agents, known limitations (no auth on admin page, no PSTN calling, no cancel/reschedule), and links to both live demos.
2. Confirm every file the assignment explicitly asks for is present: source code (both agents + shared engine), config/prompts (system prompts, tool schemas), and this PLAN.md itself as the "workflow/automation logic" artifact.
3. Do one final clean-clone test: clone the repo fresh into a new folder, follow your own README from scratch, and see if it actually works — this catches "works on my machine" gaps before a reviewer finds them.

**Definition of Done (verify before moving on):**
1. Run: `git clone <your-repo-url> /tmp/fresh-test && cd /tmp/fresh-test` and follow the README's setup steps exactly as written.
2. Expected result: both agents run locally (or you confirm the deployed links work) without needing any undocumented step you only remembered because you built it.
3. If it doesn't match: fix the README, not your memory of how it works — the gap is the deliverable's problem to solve, not something to explain verbally in a demo.

**Watch out for:** Assuming graders will "just know" to scan `.env.example` for required variables — spell out every required environment variable explicitly in the README, with a one-line description of where to get each one (Groq console, Uplift AI dashboard, Supabase project settings).

---

## Splitting into issues

Each phase above is written to be copy-pasted as a standalone issue/ticket. When starting implementation:
1. Copy one "### Phase N" section as the issue body.
2. Title it `[Phase N] [Name]`.
3. Note the dependency line so your tracker sequences them correctly.
4. Work phase by phase, verifying the Definition of Done before opening the next issue.

## Alternatives Considered

**Meta's official WhatsApp Cloud API instead of Baileys.** Genuinely free for this use case (customer-initiated conversations are unlimited/free) and carries zero ban risk, unlike Baileys' unofficial protocol access. Rejected per your explicit preference for Baileys — documented here so that if the spare number ever gets flagged/banned, the fix is "migrate `whatsapp-agent/` to Meta's Cloud API," not "start the whole project over." Because the Booking API (Phase 4) is channel-agnostic, this migration would only touch `whatsapp-agent/`, nothing else — a direct benefit of the architecture chosen.

**Real PSTN phone calling instead of browser/WebRTC voice.** Would require a paid telephony leg (Twilio/Telnyx number + per-minute rates) on top of Uplift AI regardless of provider — incompatible with the $0 budget constraint. Rejected; browser/WebRTC is Uplift AI's own official demo pattern, so it's not a corner-cut, it's the documented correct way to use their product for free.

**A single monolithic agent handling both channels in one process/language**, instead of two separate front-ends sharing one Booking API. Rejected because it would force either Baileys into Python (immature bindings) or LiveKit into Node (less mature SDK than Python) — the two-front-ends-one-shared-core architecture lets each channel use its ecosystem's best-supported tools while still guaranteeing consistency through the shared database and API, which is the actual hard problem this project is demonstrating a solution to.

**Letting the LLM decide booking confirmation directly (no separate deterministic engine).** This is the fastest way to build a demo and the most common tutorial-grade approach — and the most-cited production failure mode in 2026 postmortems for exactly this class of system (booking/scheduling agents). Rejected outright; the FOR UPDATE-guarded booking engine (Phase 3) exists specifically because "the model said it's confirmed" is not a safety guarantee.
