-- shared/schema.sql
-- Phase 2: fixed slot grid + bookings, with a structural double-booking guard.

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
    held_at TIMESTAMPTZ,                       -- lets a later phase expire stale holds
    UNIQUE (slot_date, slot_time),
    CHECK (status IN ('available', 'held', 'booked'))
);

CREATE TABLE bookings (
    id SERIAL PRIMARY KEY,
    slot_id INTEGER UNIQUE NOT NULL REFERENCES slots(id),
    customer_name TEXT NOT NULL,
    phone_number TEXT NOT NULL,
    vehicle_type TEXT NOT NULL,
    channel TEXT NOT NULL, -- 'whatsapp' | 'voice'
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (channel IN ('whatsapp', 'voice'))
);

-- Security: the app connects as the table owner, which bypasses RLS, so it keeps
-- working normally. But the public PostgREST `anon` role must NEVER be able to
-- dump customer phone numbers via the Data API, so these tables are RLS-enabled
-- with no policies — that denies anon/authenticated access entirely.
ALTER TABLE slots ENABLE ROW LEVEL SECURITY;
ALTER TABLE bookings ENABLE ROW LEVEL SECURITY;
