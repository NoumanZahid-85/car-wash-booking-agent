# booking-api/engine.py
import psycopg2

# Why FOR UPDATE: without it, two connections can both read status='available'
# before either writes, and both proceed to book — the classic
# read-then-write race condition. FOR UPDATE makes the second connection
# wait for the first transaction to finish before it's even allowed to read.
# This is the deterministic core the whole project's safety depends on.

# How long a hold survives before it's treated as abandoned and recycled.
# A customer who holds a slot but never confirms (walk-away, crash, rate-limit
# interruption) must not block that slot forever.
HOLD_TTL_MINUTES = 10


def _is_expired_hold(conn, cur, slot_id: int, status: str, held_at):
    """True when the slot is in 'held' state but the hold is older than TTL
    (abandoned). Assumes the row is already locked FOR UPDATE by the caller."""
    if status != "held":
        return False
    if held_at is None:
        return False
    cur.execute(
        "SELECT now() - held_at > make_interval(mins => %s) FROM slots WHERE id = %s",
        (HOLD_TTL_MINUTES, slot_id),
    )
    expired = cur.fetchone()[0]
    return bool(expired)


def get_available_slots(conn, slot_date):
    # Read-only: no FOR UPDATE needed, this makes no state change. Expired
    # holds are treated as available so an abandoned hold frees the slot.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, slot_time, status, held_at FROM slots "
            "WHERE slot_date = %s ORDER BY slot_time",
            (slot_date,),
        )
        out = []
        for row in cur.fetchall():
            id_, time_, status, held_at = row
            if status == "available":
                out.append({"id": id_, "time": time_.strftime("%H:%M")})
            elif _is_expired_hold(conn, cur, id_, status, held_at):
                out.append({"id": id_, "time": time_.strftime("%H:%M")})
        return out


def hold_slot(conn, slot_id: int) -> bool:
    """Temporarily reserve a slot while the conversation collects the rest
    of the customer's details. Returns False if it was already taken."""
    with conn:  # auto-commits on success, auto-rolls-back on exception
        with conn.cursor() as cur:
            cur.execute("SELECT status, held_at FROM slots WHERE id = %s FOR UPDATE", (slot_id,))
            row = cur.fetchone()
            if row is None:
                return False
            status, held_at = row
            if status != "available" and not _is_expired_hold(conn, cur, slot_id, status, held_at):
                return False
            cur.execute(
                "UPDATE slots SET status = 'held', held_at = now() WHERE id = %s",
                (slot_id,),
            )
            return True


def confirm_booking(conn, slot_id: int, customer_name: str, phone_number: str, vehicle_type: str, channel: str) -> bool:
    """Turn a held slot into a real booking. Returns False if the slot
    wasn't in 'held' state (expired, or never held)."""
    with conn:  # auto-commits on success, auto-rolls-back on exception
        with conn.cursor() as cur:
            cur.execute("SELECT status, held_at FROM slots WHERE id = %s FOR UPDATE", (slot_id,))
            row = cur.fetchone()
            if row is None:
                return False
            status, held_at = row
            # An abandoned hold must be re-held before it can be confirmed;
            # otherwise a stale conversation could claim a slot that get_available_slots
            # already offered to a new customer.
            if status != "held" or _is_expired_hold(conn, cur, slot_id, status, held_at):
                return False
            cur.execute("UPDATE slots SET status = 'booked' WHERE id = %s", (slot_id,))
            cur.execute(
                "INSERT INTO bookings (slot_id, customer_name, phone_number, vehicle_type, channel) "
                "VALUES (%s, %s, %s, %s, %s)",
                (slot_id, customer_name, phone_number, vehicle_type, channel),
            )
            return True
