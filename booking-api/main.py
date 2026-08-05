# booking-api/main.py
import os
import pathlib
from datetime import date, timedelta

import psycopg2
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from engine import confirm_booking, get_available_slots, hold_slot

load_dotenv()

_ADMIN_HTML = pathlib.Path(__file__).parent / "admin.html"

# Why typed Pydantic models: when Groq's function-calling generates a tool
# call, FastAPI will reject a malformed one (wrong types, missing fields)
# with a clear 422 error instead of your engine code getting garbage input
# and doing something undefined. This is where "typed tools cut malformed
# calls" from the research becomes real, not just a design slogan.

app = FastAPI()


def get_conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def _slot_details(conn, slot_id):
    """Return the date/time for a slot id, or None if the id doesn't exist.
    Lets the agent report the exact booked slot instead of guessing."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT slot_date, slot_time FROM slots WHERE id = %s", (slot_id,)
        )
        row = cur.fetchone()
    if row is None:
        return {}
    return {
        "slot_date": str(row[0]),
        "slot_time": row[1].strftime("%H:%M"),
    }


def generate_next_days_map():
    today = date.today()
    out = []
    weekday_names = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    for i in range(7):
        d = today + timedelta(days=i)
        iso = d.strftime("%Y-%m-%d")
        weekday = weekday_names[(d.weekday() + 1) % 7]
        if i == 0:
            ordinal = "today"
        elif i == 1:
            ordinal = "tomorrow"
        else:
            ordinal = f"{weekday} ({i} days from now)"
        out.append(f"- {iso} is {weekday} ({ordinal})")
    return "\n".join(out)

def get_system_prompt(channel: str) -> str:
    today_iso = date.today().strftime("%Y-%m-%d")
    
    language_note = ""
    if channel == "whatsapp":
        language_note = "The customer is messaging from WhatsApp and will write in English. Always reply in English."
    elif channel in ("voice", "uplift", "voice-agent"):
        language_note = "The customer is speaking over the voice channel and may speak in Urdu. Even so, always reply in English -- never write or speak in Urdu."
    
    return f"""You are the booking assistant for Sparkle Car Wash, a single-location car wash.
{language_note}

A booking needs: customer name, vehicle type, preferred date, preferred time, and phone number.
Follow this workflow:
1. If the customer asks about availability or wants to book, call list_available_slots with the date they want (ask which date first if they have not given one; today's date is {today_iso}).
2. When a slot is chosen, call hold_slot with its id. If a slot is already held in this conversation (you see 'held successfully' in the tool response history), do NOT call hold_slot again.
3. Collect any missing details (name, vehicle type, phone number) with short friendly questions. Record the vehicle type exactly as the customer described it (e.g. keep "Corolla", "Civic", "SUV" -- do not generalize to "Sedan").
4. Only once you have everything, call confirm_booking with the held slot id and the details.
5. If confirm_booking returns success: true, tell the customer their booking is confirmed with the EXACT date and time given in the tool result (the tool tells you which slot was booked -- trust the tool result over your own assumption).
6. If confirm_booking returns success: false (slot was just taken), apologize and offer to check other available times.

Date handling (important):
- Use the exact YYYY-MM-DD from the day map below to resolve weekday names. Never do day-of-week arithmetic yourself.
- Upcoming days (today and the next 6 days):
{generate_next_days_map()}

Choosing the right slot:
- The customer speaks in everyday time ("2pm", "around noon", "دو بجے"). Convert it to 24-hour time before matching: 2pm = 14:00, 12pm = 12:00, 3pm = 15:00, etc.
- Pick the slot whose time equals the customer's requested time. If their request is ambiguous or no slot matches, ask a clarifying question showing only the available times (e.g. "We have 10:00, 11:00 and 14:00 available -- which suits you?"). Do NOT show raw slot ids to the customer.
- Before confirming, double check the slot you are about to confirm is the one whose time matches what the customer asked for.

Hard rules:
- NEVER tell the customer their booking is confirmed unless a confirm_booking tool call actually returned success: true. If you only intend to book, say you are reserving/checking, not that it is done.
- NEVER call confirm_booking before the customer has explicitly provided their name and (unless using the provided WhatsApp/voice fallback number) their phone number. A real phone number must be present in the conversation before confirming.
- NEVER invent slot ids, dates, or times that did not come from a tool result. When reporting a booking, use the date/time exactly as returned by the tools.
- NEVER invent a phone number; use the customer's WhatsApp/voice number noted in the conversation if they have not given a different one.
- NEVER show slot ids to the customer -- talk only in friendly times like "2:00 PM".
- NEVER repeat raw tool output back to the customer. After a successful hold_slot, respond conversationally like "I've reserved 2:00 PM for you" -- never mention "slot 33" or "held successfully".
- If the customer already has a confirmed booking from this conversation (you told them it was confirmed), acknowledge it on follow-up messages ("your booking is still confirmed for ..."). Do NOT say their slot was taken by someone else.
- If the customer asks for something not related to booking a car wash, politely redirect.
- Keep replies short and conversational, 1-3 sentences.
"""

@app.get("/prompt")
def get_prompt_endpoint(channel: str):
    return {"prompt": get_system_prompt(channel)}


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
    conn = get_conn()
    try:
        return get_available_slots(conn, slot_date)
    finally:
        conn.close()


@app.post("/hold")
def hold(req: HoldRequest):
    conn = get_conn()
    try:
        success = hold_slot(conn, req.slot_id)
        slot = _slot_details(conn, req.slot_id)
        return {"success": success, **slot}
    finally:
        conn.close()


@app.post("/confirm")
def confirm(req: ConfirmRequest):
    conn = get_conn()
    try:
        success = confirm_booking(
            conn,
            req.slot_id,
            req.customer_name,
            req.phone_number,
            req.vehicle_type,
            req.channel,
        )
        slot = _slot_details(conn, req.slot_id)
        return {"success": success, **slot}
    finally:
        conn.close()


@app.get("/admin/data")
def admin_data():
    """JSON payload for the Station Manager dashboard: the next six service
    days with their full slot grid, all bookings, and headline stats."""
    conn = get_conn()
    try:
        today = date.today()

        # Next six service days, Mon-Sat, Sunday closed (mirrors the seeder).
        service_days = []
        d = today
        while len(service_days) < 6:
            if d.weekday() != 6:  # Sunday
                service_days.append(d)
            d += timedelta(days=1)

        with conn.cursor() as cur:
            cur.execute(
                "SELECT s.id, s.slot_date, s.slot_time, s.status, b.customer_name "
                "FROM slots s LEFT JOIN bookings b ON b.slot_id = s.id "
                "WHERE s.slot_date >= %s AND s.slot_date <= %s "
                "ORDER BY s.slot_date, s.slot_time",
                (service_days[0].isoformat(), service_days[-1].isoformat()),
            )
            rows = cur.fetchall()
            cur.execute(
                "SELECT b.id, s.slot_date, s.slot_time, b.customer_name, "
                "       b.phone_number, b.vehicle_type, b.channel, b.created_at "
                "FROM bookings b JOIN slots s ON b.slot_id = s.id "
                "ORDER BY s.slot_date, s.slot_time"
            )
            booking_rows = cur.fetchall()

        by_day = {day.isoformat(): [] for day in service_days}
        for slot_id, slot_date, slot_time, status, name in rows:
            by_day[str(slot_date)].append(
                {
                    "id": slot_id,
                    "time": slot_time.strftime("%H:%M"),
                    "status": status,
                    "name": name,
                }
            )

        month_names = [
            "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        ]
        days = [
            {
                "iso": day.isoformat(),
                "weekday": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][day.weekday()],
                "month_day": f"{month_names[day.month - 1]} {day.day}",
                "is_today": day == today,
                "slots": by_day[day.isoformat()],
            }
            for day in service_days
        ]

        bookings = [
            {
                "id": b[0],
                "slot_date": str(b[1]),
                "slot_time": b[2].strftime("%H:%M"),
                "customer_name": b[3],
                "phone_number": b[4],
                "vehicle_type": b[5],
                "channel": b[6],
                "created_at": b[7].isoformat(),
            }
            for b in booking_rows
        ]

        held = sum(1 for r in rows if r[3] == "held")
        booked = sum(1 for r in rows if r[3] == "booked")
        open_ = sum(1 for r in rows if r[3] == "available")
        today_bookings = sum(1 for b in bookings if b["slot_date"] == today.isoformat())

        return {
            "today": today.isoformat(),
            "days": days,
            "bookings": bookings,
            "stats": {
                "today_bookings": today_bookings,
                "held": held,
                "booked_week": booked,
                "open_week": open_,
            },
        }
    finally:
        conn.close()


@app.post("/admin/reset/{slot_id}")
def admin_reset(slot_id: int):
    """Admin/testing helper: free a held or booked slot so the engine can be
    exercised repeatedly without filling up the week. Deletes the booking
    record and restores the slot to 'available'."""
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM slots WHERE id = %s", (slot_id,))
                if cur.fetchone() is None:
                    return {"success": False, "reason": "no such slot"}
                cur.execute("DELETE FROM bookings WHERE slot_id = %s", (slot_id,))
                cur.execute(
                    "UPDATE slots SET status = 'available', held_at = NULL "
                    "WHERE id = %s",
                    (slot_id,),
                )
        return {"success": True}
    finally:
        conn.close()


@app.get("/admin/bookings", response_class=HTMLResponse)
def admin_bookings():
    # Why a single self-contained HTML file: this is an internal tool for you,
    # the business owner — no build step, no framework, just one file that
    # fetches /admin/data and renders the live wash schedule. The dashboard is
    # the same page the agents write to, so a slot freed here is instantly gone
    # on WhatsApp and Voice.
    try:
        page = _ADMIN_HTML.read_text(encoding="utf-8")
    except FileNotFoundError:
        page = "<html><body><h1>admin.html missing next to main.py</h1></body></html>"
    return HTMLResponse(page)
