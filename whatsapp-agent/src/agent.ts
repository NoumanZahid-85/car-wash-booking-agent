// whatsapp-agent/src/agent.ts
import Groq from "groq-sdk";
import axios from "axios";
import path from "node:path";
import { config as loadEnv } from "dotenv";

// Load the project-root .env (the bot runs from whatsapp-agent/, the .env
// lives one level up). dotenv handles the quoted values and CRLF endings
// that a hand-rolled parser gets wrong.
loadEnv({ path: path.resolve(process.cwd(), "..", ".env") });
loadEnv(); // also allow whatsapp-agent/.env to override for local dev

const groq = new Groq({ apiKey: process.env.GROQ_API_KEY });
const BOOKING_API = (
  process.env.BOOKING_API_URL || "http://localhost:8000"
).replace(/\/$/, "");
const MODEL = process.env.MODEL || "llama-3.3-70b-versatile";

// Why tools mirror the Booking API 1:1: the LLM should never have a tool
// that does more than the API allows -- if hold_slot the tool can only ever
// call POST /hold, there's no path for the model to "decide" a booking is
// confirmed without the deterministic engine's row lock actually agreeing.

const tools = [
  {
    type: "function" as const,
    function: {
      name: "list_available_slots",
      description:
        "Get available car wash slots for a given date (YYYY-MM-DD). Returns slot ids and times.",
      parameters: {
        type: "object",
        properties: {
          date: {
            type: "string",
            description: "Date in YYYY-MM-DD format, e.g. 2026-08-10",
          },
        },
        required: ["date"],
      },
    },
  },
  {
    type: "function" as const,
    function: {
      name: "hold_slot",
      description:
        "Temporarily reserve a slot by its id while collecting the rest of the customer details. Returns true if reserved, false if the slot was already taken. Call this BEFORE confirm_booking.",
      parameters: {
        type: "object",
        properties: {
          slot_id: {
            type: "integer",
            description: "The slot id returned by list_available_slots",
          },
        },
        required: ["slot_id"],
      },
    },
  },
  {
    type: "function" as const,
    function: {
      name: "confirm_booking",
      description:
        "Turn a held slot into a real confirmed booking. Returns success only if the slot was previously held. Only after this returns success: true may you tell the customer they are booked.",
      parameters: {
        type: "object",
        properties: {
          slot_id: {
            type: "integer",
            description: "The slot id that was held",
          },
          customer_name: { type: "string", description: "Customer full name" },
          phone_number: {
            type: "string",
            description:
              "Customer phone number in international format, e.g. +923001234567",
          },
          vehicle_type: {
            type: "string",
            description: "Customer vehicle type, e.g. Sedan, SUV, Hatchback",
          },
        },
        required: ["slot_id", "customer_name", "phone_number", "vehicle_type"],
      },
    },
  },
];

export type Channel = "whatsapp" | "uplift";

// The language instruction differs per channel. The bot is not good at
// writing Urdu, so it must ALWAYS reply in English on both channels --
// even when the customer speaks Urdu, the reply stays in English.
const LANGUAGE_NOTE: Record<Channel, string> = {
  whatsapp:
    "The customer is messaging from WhatsApp and will write in English. Always reply in English.",
  uplift:
    "The customer is speaking over the voice channel and may speak in Urdu. Even so, always reply in English -- never write in Urdu.",
};

export interface HandleOptions {
  channel?: Channel;
}

function buildSystemPrompt(channel: Channel): string {
  return `You are the booking assistant for Sparkle Car Wash, a single-location car wash.
${LANGUAGE_NOTE[channel]}

A booking needs: customer name, vehicle type, preferred date, preferred time, and phone number.
Follow this workflow:
1. If the customer asks about availability or wants to book, call list_available_slots with the date they want (ask which date first if they have not given one; today's date is ${new Date().toISOString().slice(0, 10)}).
2. When a slot is chosen, call hold_slot with its id. If a slot is already held in this conversation (you see 'held successfully' in the tool response history), do NOT call hold_slot again.
3. Collect any missing details (name, vehicle type, phone number) with short friendly questions. Record the vehicle type exactly as the customer described it (e.g. keep "Corolla", "Civic", "SUV" -- do not generalize to "Sedan").
4. Only once you have everything, call confirm_booking with the held slot id and the details.
5. If confirm_booking returns success: true, tell the customer their booking is confirmed with the EXACT date and time given in the tool result (the tool tells you which slot was booked -- trust the tool result over your own assumption).
6. If confirm_booking returns success: false (slot was just taken), apologize and offer to check other available times.

Date handling (important):
- Use the exact YYYY-MM-DD from the day map below to resolve weekday names. Never do day-of-week arithmetic yourself.
- Upcoming days (today and the next 6 days):
${nextDaysMap()}

Choosing the right slot:
- The customer speaks in everyday time ("2pm", "around noon", "دو بجے"). Convert it to 24-hour time before matching: 2pm = 14:00, 12pm = 12:00, 3pm = 15:00, etc.- Pick the slot whose time equals the customer's requested time. If their request is ambiguous or no slot matches, ask a clarifying question showing only the available times (e.g. "We have 10:00, 11:00 and 14:00 available -- which suits you?"). Do NOT show raw slot ids to the customer.
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
- Keep replies short and conversational, 1-3 sentences.`;
}

// transient turn state is now managed inside the handleMessage execution context
// to prevent race conditions during concurrent requests.

// Generates an explicit date -> weekday map for the next 7 days so the model
// resolves "Saturday"/"اتوار" to a real date without doing arithmetic itself.
function nextDaysMap(): string {
  const names = [
    "Sunday",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
  ];
  const out: string[] = [];
  const now = new Date();
  for (let i = 0; i < 7; i++) {
    const d = new Date(now);
    d.setDate(now.getDate() + i);
    const iso = d.toISOString().slice(0, 10);
    const weekday = names[d.getUTCDay()];
    const ordinal =
      i === 0
        ? "today"
        : i === 1
          ? "tomorrow"
          : `${weekday} (${i} days from now)`;
    out.push(`- ${iso} is ${weekday} (${ordinal})`);
  }
  return out.join("\n");
}

// Per-conversation history. In-memory: resets when the process restarts --
// fine for v1, documented in the README as a known limitation.
const conversations = new Map<string, any[]>();

async function getHistory(phoneNumber: string, channel: Channel): Promise<any[]> {
  let history = conversations.get(phoneNumber);
  if (!history) {
    // Seed the prompt with the customer's real WhatsApp number so the model
    // never has to invent one (the tool requires it, and inventing one would
    // be both wrong and a data-quality bug).
    const numberFromJid = phoneNumber.split("@")[0];
    let sysPrompt = "";
    try {
      const resp = await axios.get(`${BOOKING_API}/prompt`, {
        params: { channel },
        timeout: 10000,
      });
      sysPrompt = resp.data.prompt;
    } catch (err) {
      console.error("Error fetching system prompt from API, falling back:", err);
      sysPrompt = buildSystemPrompt(channel);
    }
    const historySeed = [
      { role: "system", content: sysPrompt },
      {
        role: "system",
        content:
          `The customer's ${channel === "uplift" ? "voice/phone" : "WhatsApp"} number is ${numberFromJid}. ` +
          `If the customer does not provide a phone number, use this one in confirm_booking.`,
      },
    ];
    history = historySeed;
    conversations.set(phoneNumber, history);
  }
  return history;
}

async function callTool(
  name: string,
  args: any,
  channel: Channel,
  ctx: { confirmSuccess: boolean },
): Promise<string> {
  try {
    if (name === "list_available_slots") {
      const { data } = await axios.get(`${BOOKING_API}/slots`, {
        params: { slot_date: args.date },
        timeout: 20000,
      });
      if (!Array.isArray(data) || data.length === 0) {
        return "No slots are available on that date. Suggest the customer pick another day.";
      }
      return data.map((s: any) => `slot_id ${s.id}: ${s.time}`).join(", ");
    }
    if (name === "hold_slot") {
      const { data } = await axios.post(
        `${BOOKING_API}/hold`,
        { slot_id: args.slot_id },
        { timeout: 20000 },
      );
      return data.success
        ? `Slot ${args.slot_id} (${data.slot_date} at ${data.slot_time}) held successfully.`
        : `Failed: slot ${args.slot_id} is no longer available (already taken).`;
    }
    if (name === "confirm_booking") {
      const { data } = await axios.post(
        `${BOOKING_API}/confirm`,
        {
          slot_id: args.slot_id,
          customer_name: args.customer_name,
          phone_number: args.phone_number,
          vehicle_type: args.vehicle_type,
          channel,
        },
        { timeout: 20000 },
      );
      if (data.success) {
        ctx.confirmSuccess = true;
      }
      return data.success
        ? `Booking confirmed successfully for slot ${args.slot_id} (${data.slot_date} at ${data.slot_time}). Use EXACTLY this date and time in your reply.`
        : `Failed: slot ${args.slot_id} was not in a holdable/bookable state (likely just taken by someone else).`;
    }
    return `Unknown tool: ${name}`;
  } catch (err: any) {
    const detail = err?.response?.data
      ? JSON.stringify(err.response.data)
      : err?.message;
    return `Tool error calling ${name}: ${detail}`;
  }
}

// Extracts Groq-style inline tool calls of the form
// <function=name>{"arg": "value"}</function> (and the mangled variant
// <function=name":{"arg": ...} — note the stray quote after the name).
function extractInlineFunctionCalls(
  text: string,
): { id: string; name: string; args: any }[] {
  const calls: { id: string; name: string; args: any }[] = [];
  const re = /<function=([A-Za-z_]+)"?\s*>(.*?)<\/function>/gs;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    const name = m[1];
    let args: any = {};
    try {
      const raw = m[2].trim();
      // Handle both {"a":1} and {"a":1} with leading/trailing garbage
      const firstBrace = raw.indexOf("{");
      const lastBrace = raw.lastIndexOf("}");
      if (firstBrace >= 0 && lastBrace > firstBrace) {
        args = JSON.parse(raw.slice(firstBrace, lastBrace + 1));
      }
    } catch {
      args = {};
    }
    calls.push({ id: `inline_${calls.length + 1}`, name, args });
  }
  return calls;
}

// Helper to scan history for a successful booking confirmation
function getConfirmedBooking(
  history: any[],
): { date: string; time: string; name: string } | null {
  for (let i = history.length - 1; i >= 0; i--) {
    const msg = history[i];
    if (msg.content && msg.content.includes("Booking confirmed successfully")) {
      const match = msg.content.match(/\(([^)]+)\)/);
      if (match) {
        const parts = match[1].split(" at ");
        if (parts.length === 2) {
          const date = parts[0];
          const time = parts[1];
          let name = "";
          for (let j = i - 1; j >= 0; j--) {
            const assoc = history[j];
            if (assoc.role === "assistant" && assoc.tool_calls) {
              const tc = assoc.tool_calls.find(
                (c: any) => c.function.name === "confirm_booking",
              );
              if (tc) {
                try {
                  const args =
                    typeof tc.function.arguments === "string"
                      ? JSON.parse(tc.function.arguments)
                      : tc.function.arguments;
                  name = args.customer_name || "";
                } catch {}
                break;
              }
            }
            if (
              assoc.role === "assistant" &&
              assoc.content &&
              assoc.content.includes("confirm_booking")
            ) {
              const inlineIc = extractInlineFunctionCalls(assoc.content);
              const tc = inlineIc.find(
                (c: any) => c.name === "confirm_booking",
              );
              if (tc && tc.args && tc.args.customer_name) {
                name = tc.args.customer_name;
                break;
              }
            }
          }
          return { date, time, name };
        }
      }
    }
  }
  return null;
}

export async function handleMessage(
  phoneNumber: string,
  text: string,
  opts?: HandleOptions,
): Promise<string> {
  const channel: Channel = opts?.channel ?? "whatsapp";
  const history = await getHistory(phoneNumber, channel);

  // Remove any previously appended [system] confirm reminder messages to keep history clean
  for (let i = history.length - 1; i >= 0; i--) {
    if (
      history[i].role === "system" &&
      history[i].content &&
      history[i].content.includes("active confirmed booking")
    ) {
      history.splice(i, 1);
    }
  }

  const prevConfirmed = getConfirmedBooking(history);
  if (prevConfirmed) {
    history.push({
      role: "system",
      content:
        `The customer has an active confirmed booking for ${prevConfirmed.name} on ${prevConfirmed.date} at ${prevConfirmed.time}. ` +
        `Under NO circumstances should you call any tools (list_available_slots, hold_slot, confirm_booking) if they reference this booking or ask if it is confirmed. ` +
        `Simply reply and assure the customer that their booking is active and secured.`,
    });
  }

  history.push({ role: "user", content: text });

  let finalReply: string | null = null;
  // Clean context state for this turn. confirmSuccess flips to true only
  // when a confirm_booking tool call actually returns success.
  const turnContext = { confirmSuccess: false };

  // Function-calling loop: call the model, run any requested tools, feed the
  // results back, and repeat until the model returns plain text (max 5 hops
  // so a pathological tool loop can't run forever).
  for (let hop = 0; hop < 5 && !finalReply; hop++) {
    let completion;
    let attempts = 0;
    while (attempts < 5) {
      try {
        completion = await groq.chat.completions.create({
          model: MODEL,
          messages: history,
          tools,
          tool_choice: "auto",
        });
        break;
      } catch (err: any) {
        const code = err?.error?.error?.code;
        const failedGen = err?.error?.error?.failed_generation;
        console.error(
          "Groq call failed (code=" + code + ") generation=",
          failedGen,
        );
        if (code === "rate_limit_exceeded") {
          attempts++;
          let waitMs = 4000;
          const errMsg = err?.message || err?.error?.error?.message || "";
          const match = errMsg.match(/try again in (\d+(\.\d+)?)(s|ms|m)/i);
          if (match) {
            const num = parseFloat(match[1]);
            const unit = match[3].toLowerCase();
            if (unit === "s") {
              waitMs = Math.ceil((num + 0.5) * 1000);
            } else if (unit === "ms") {
              waitMs = Math.ceil(num + 500);
            } else if (unit === "m") {
              waitMs = Math.ceil((num * 60 + 0.5) * 1000);
            }
          }
          console.warn(
            `Rate limit hit. Waiting for ${waitMs}ms before retry... Message: ${errMsg}`,
          );
          await new Promise((r) => setTimeout(r, waitMs));
          continue;
        }
        if (code === "tool_use_failed") {
          history.push({
            role: "user",
            content:
              "Your last function call was malformed and rejected. Re-issue the correct function call with valid JSON arguments.",
          });
          break;
        }
        finalReply =
          "Sorry, I ran into a technical glitch. Could you repeat that?";
        break;
      }
    }

    if (finalReply) {
      break;
    }

    if (!completion) {
      continue;
    }

    const msg = completion!.choices[0]?.message;
    if (!msg) {
      finalReply =
        "Sorry, I had a problem understanding that. Could you repeat it?";
      break;
    }

    // Some Groq Llama variants emit tool calls as <function=name>{"..."}</function>
    // markup inside the text content instead of the structured tool_calls field,
    // especially when the JSON got mangled. If we see that markup, execute it
    // like a normal tool call rather than relaying raw markup to the customer.
    const inlineCalls = msg.content
      ? extractInlineFunctionCalls(msg.content)
      : [];
    if (!msg.tool_calls || msg.tool_calls.length === 0) {
      if (inlineCalls.length > 0) {
        history.push({
          role: "assistant",
          content: msg.content || null,
        });
        // No structured tool_calls exist for these, so relay the results as a
        // system/user note instead of role:'tool' (which the API would reject
        // without a matching tool_call_id in the assistant message).
        const results = [];
        for (const ic of inlineCalls) {
          const result = await callTool(ic.name, ic.args, channel, turnContext);
          results.push(`${ic.name} returned: ${result}`);
        }
        history.push({
          role: "user",
          content: `[system note] ${results.join(" | ")}`,
        });
        continue;
      }
      const textReply = (msg.content || "")
        .replace(/<function=[^>]*>.*?<\/function>/gs, "")
        .trim();
      if (!textReply || textReply === "...") {
        // The model produced empty/placeholder content. Ask it to actually
        // reply rather than emitting "..." back to the customer.
        history.push({
          role: "user",
          content:
            "Please reply to the customer now with a short, friendly message. Do not call any functions unless you truly need more information.",
        });
        continue;
      }
      // Guard against the model claiming a confirmation that no tool call
      // actually produced (seen after rate-limited/aborted turns).
      if (
        !turnContext.confirmSuccess &&
        /\b(confirm|confirmed|booked|successfully booked)\b/i.test(textReply)
      ) {
        history.push({
          role: "user",
          content:
            "Important: no booking has actually been confirmed -- no confirm_booking call succeeded. " +
            "Do NOT claim the booking is confirmed. Either call confirm_booking now (if the slot is held and you have all details) " +
            "or tell the customer it is not booked yet and ask for what you need.",
        });
        continue;
      }
      finalReply = textReply;
      break;
    }

    if (msg.tool_calls && msg.tool_calls.length > 0) {
      // Record the assistant's tool-call message so the API can correlate
      // the tool responses below with it.
      history.push({
        role: "assistant",
        content: msg.content || null,
        tool_calls: msg.tool_calls.map((tc) => ({
          id: tc.id,
          type: tc.type,
          function: {
            name: tc.function.name,
            arguments: tc.function.arguments,
          },
        })),
      });

      for (const tc of msg.tool_calls) {
        let args: any = {};
        try {
          args = JSON.parse(tc.function.arguments || "{}");
        } catch {
          args = {};
        }
        const result = await callTool(
          tc.function.name,
          args,
          channel,
          turnContext,
        );
        history.push({ role: "tool", tool_call_id: tc.id, content: result });
      }
      // Loop back to the model with the tool results.
    } else {
      finalReply = msg.content || "...";
    }
  }

  if (!finalReply) {
    finalReply =
      "I seem to be having trouble finishing that request. Please try again.";
  }

  history.push({ role: "assistant", content: finalReply });
  return finalReply;
}

export function resetConversation(phoneNumber: string) {
  conversations.delete(phoneNumber);
}
