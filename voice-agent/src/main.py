import os
import logging
import re
import httpx
from datetime import date, datetime, timedelta
from dotenv import load_dotenv

# Load root-level or local dotenv
if os.path.exists("../.env"):
    load_dotenv("../.env")
else:
    load_dotenv()

from livekit import rtc
from livekit.agents import AgentSession, JobContext, WorkerOptions, cli, function_tool
from livekit.agents.voice import Agent
from livekit.plugins import openai, silero, upliftai

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOOKING_API = os.environ.get("BOOKING_API_URL", "http://localhost:8100").rstrip("/")

def next_days_map() -> str:
    # 7-day mapping
    today = date.today()
    out = []
    weekday_names = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
    for i in range(7):
        d = today + timedelta(days=i)
        iso = d.strftime("%Y-%m-%d")
        weekday = weekday_names[(d.weekday() + 1) % 7]
        day_num = d.day
        if day_num in [1, 21, 31]:
            suffix = 'st'
        elif day_num in [2, 22]:
            suffix = 'nd'
        elif day_num in [3, 23]:
            suffix = 'rd'
        else:
            suffix = 'th'
        ordinal = f"{day_num}{suffix}"
        out.append(f"- {iso} is {weekday} ({ordinal})")
    return "\n".join(out)

# Voice system prompt instructions
SYSTEM_PROMPT = f"""You are the friendly voice assistant for Sparkle Car Wash, a single-location car wash.
The customer is speaking over the voice channel and may speak in Urdu. Even so, always reply in English -- never write or speak in Urdu.

A booking needs: customer name, vehicle type, preferred date, preferred time, and phone number.
Follow this workflow:
1. If the customer asks about availability or wants to book, call list_available_slots with the date they want (ask which date first if they have not given one; today's date is {date.today().strftime("%Y-%m-%d")}).
2. When a slot is chosen, call hold_slot with its id. If a slot is already held in this conversation (you see 'held successfully' in the tool response history), do NOT call hold_slot again.
3. Collect any missing details (name, vehicle type, phone number) with short friendly questions. Record the vehicle type exactly as the customer described it (e.g. keep "Corolla", "Civic", "SUV" -- do not generalize to "Sedan").
4. Only once you have everything, call confirm_booking with the held slot id and the details.
5. If confirm_booking returns success: true, tell the customer their booking is confirmed with the EXACT date and time given in the tool result (the tool tells you which slot was booked -- trust the tool result over your own assumption).
6. If confirm_booking returns success: false (slot was just taken), apologize and offer to check other available times.

Date handling (important):
- Use the exact YYYY-MM-DD from the day map below to resolve weekday names. Never do day-of-week arithmetic yourself.
- Upcoming days (today and the next 6 days):
{next_days_map()}

Choosing the right slot:
- The customer speaks in everyday time ("2pm", "around noon"). Convert it to 24-hour time before matching: 2pm = 14:00, 12pm = 12:00, 3pm = 15:00, etc.
- Pick the slot whose time equals the customer's requested time. If their request is ambiguous or no slot matches, ask a clarifying question showing only the available times (e.g. "We have 10:00, 11:00 and 14:00 available -- which suits you?"). Do NOT show raw slot ids to the customer.
- Before confirming, double check the slot you are about to confirm is the one whose time matches what the customer asked for.

Hard rules:
- NEVER tell the customer their booking is confirmed unless a confirm_booking tool call actually returned success: true. If you only intend to book, say you are reserving/checking, not that it is done.
- NEVER call confirm_booking before the customer has explicitly provided their name. Use the phone number of the participant if available, or ask for one.
- You must maintain English language at all times.
"""


class CarWashVoiceTools:
    """Booking tools the voice agent can call. confirm_success flips to True
    only when confirm_booking's API call actually returned success."""

    def __init__(self):
        self.confirm_success = False

    @function_tool(description="Get available car wash slots for a given date in YYYY-MM-DD format.")
    async def list_available_slots(self, date: str) -> str:
        url = f"{BOOKING_API}/slots?slot_date={date}"
        logger.info(f"Calling list_available_slots for {date}: {url}")
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, timeout=10.0)
                if resp.status_code != 200:
                    return f"Error from slots API (code {resp.status_code}): {resp.text}"
                slots = resp.json()
                if not slots:
                    return "No available slots on this date."
                formatted = [f"ID {s['id']} at {s['time']}" for s in slots]
                return f"Available slots for {date}: " + ", ".join(formatted)
        except Exception as e:
            logger.error(f"Error in list_available_slots: {e}")
            return f"Error calling slots API: {str(e)}"

    @function_tool(description="Hold a slot temporarily using its slot ID. Returns success only if the slot was available or had an expired hold.")
    async def hold_slot(self, slot_id: int) -> str:
        url = f"{BOOKING_API}/hold"
        logger.info(f"Calling hold_slot for {slot_id}: {url}")
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json={"slot_id": slot_id}, timeout=10.0)
                if resp.status_code != 200:
                    return f"Error holding slot (code {resp.status_code}): {resp.text}"
                data = resp.json()
                if data.get("success"):
                    return f"Slot {slot_id} ({data['slot_date']} at {data['slot_time']}) held successfully."
                return f"Failed: slot {slot_id} is already held or booked by someone else."
        except Exception as e:
            logger.error(f"Error in hold_slot: {e}")
            return f"Error holding slot: {str(e)}"

    @function_tool(description="Confirm booking for a held slot. All details (slot_id, customer_name, phone_number, vehicle_type) are required. Returns success only if the slot was previously held.")
    async def confirm_booking(
        self,
        slot_id: int,
        customer_name: str,
        phone_number: str,
        vehicle_type: str,
    ) -> str:
        url = f"{BOOKING_API}/confirm"
        logger.info(f"Calling confirm_booking for {slot_id} (name: {customer_name}, phone: {phone_number}, config: {vehicle_type})")

        payload = {
            "slot_id": slot_id,
            "customer_name": customer_name,
            "phone_number": phone_number,
            "vehicle_type": vehicle_type,
            "channel": "voice"
        }

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, timeout=10.0)
                if resp.status_code != 200:
                    return f"Error confirming slot (code {resp.status_code}): {resp.text}"
                data = resp.json()
                if data.get("success"):
                    self.confirm_success = True
                    return f"Booking confirmed successfully for slot {slot_id} ({data['slot_date']} at {data['slot_time']}). Use EXACTLY this date and time in your reply."
                return f"Failed: slot {slot_id} is not held or was already confirmed by someone else."
        except Exception as e:
            logger.error(f"Error in confirm_booking: {e}")
            return f"Error confirming booking: {str(e)}"


async def entrypoint(ctx: JobContext):
    logger.info(f"Starting voice session in room {ctx.room.name}")
    await ctx.connect()

    # Check if we can infer phone number from room participant JID / identity
    inferred_phone = "+923000000001"
    for participant in ctx.room.remote_participants.values():
        identity = participant.identity
        if identity.startswith("+") or identity.isdigit():
            inferred_phone = identity
            break

    tools = CarWashVoiceTools()

    # Fetch system prompt from booking-api
    sys_prompt = ""
    try:
        url = f"{BOOKING_API}/prompt"
        logger.info(f"Fetching system prompt from API: {url} (channel: voice)")
        resp = httpx.get(url, params={"channel": "voice"}, timeout=10.0)
        if resp.status_code == 256 or resp.status_code == 200:
            sys_prompt = resp.json().get("prompt", "")
        else:
            logger.error(f"Error fetching system prompt (code {resp.status_code}): {resp.text}")
    except Exception as e:
        logger.error(f"Failed to fetch system prompt from API, fallback to local: {e}")

    if not sys_prompt:
        sys_prompt = SYSTEM_PROMPT

    # Fold the inferred phone number into the instructions (the model must use
    # it as the fallback for confirm_booking instead of inventing one).
    instructions = (
        f"{sys_prompt}\n\nThe customer's phone number is {inferred_phone}. "
        f"If they do not provide a phone number, use this one in confirm_booking."
    )

    llm_instance = openai.LLM(
        model="llama-3.3-70b-versatile",
        api_key=os.environ["GROQ_API_KEY"],
        base_url="https://api.groq.com/openai/v1"
    )

    stt_instance = openai.STT(
        model="whisper-large-v3",
        api_key=os.environ["GROQ_API_KEY"],
        base_url="https://api.groq.com/openai/v1"
    )

    # Uplift AI TTS
    tts_instance = upliftai.TTS(
        voice_id="v_meklc281",
        api_key=os.environ["UPLIFTAI_API_KEY"]
    )

    vad_instance = silero.VAD.load()

    # Low-latency voice assistant agent loop
    session = AgentSession(
        vad=vad_instance,
        stt=stt_instance,
        llm=llm_instance,
        tts=tts_instance,
    )

    agent = Agent(
        instructions=instructions,
        tools=[tools.list_available_slots, tools.hold_slot, tools.confirm_booking],
    )

    # Custom safety hook: reject confirmation claims if confirm_booking did not succeed.
    # Fires whenever a message is committed to the conversation; if the model claims
    # a confirmation without the tool actually succeeding, inject a system warning
    # so the next turn corrects it.
    @session.on("conversation_item_added")
    def on_conversation_item(evt):
        item = evt.item
        if getattr(item, "role", None) != "assistant":
            return
        text = getattr(item, "raw_text_content", None) or ""
        if not text:
            return
        if not tools.confirm_success and any(w in text.lower() for w in ["confirm", "booked", "successfully"]):
            logger.warning("Agent claimed confirmation without tool success! Injecting system warning.")
            session.history.add_message(
                role="system",
                content="Important reminder: No booking has been confirmed yet. Do NOT claim the booking is confirmed unless confirm_booking tool has returned success: true.",
            )

    # When the confirm_booking tool succeeds, guide follow-up queries so the model
    # never calls tools again against an already-secured booking.
    @session.on("conversation_item_added")
    def on_booking_secured(evt):
        if tools.confirm_success and evt.item.role == "assistant":
            text = getattr(evt.item, "raw_text_content", None) or ""
            if any(w in text.lower() for w in ["confirmed", "booked", "secured"]):
                session.history.add_message(
                    role="system",
                    content="The customer has an active confirmed booking. Under NO circumstances should you call any tools (list_available_slots, hold_slot, confirm_booking) if they reference this booking. Affirm that it is secured.",
                )

    await session.start(agent=agent, room=ctx.room)

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
