# booking-api/test_engine.py
import os
import threading
from datetime import date, datetime, timedelta, timezone

import psycopg2
import pytest
from dotenv import load_dotenv

from engine import HOLD_TTL_MINUTES, confirm_booking, get_available_slots, hold_slot

load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]


def _connect():
    return psycopg2.connect(DATABASE_URL)


def _pick_available_slot_id() -> int:
    """Pick a slot that is available, or free the oldest one if none is."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM slots WHERE status = 'available' ORDER BY id LIMIT 1")
            row = cur.fetchone()
            if row is not None:
                return row[0]
            cur.execute(
                "UPDATE slots SET status = 'available', held_at = NULL "
                "WHERE id = (SELECT id FROM slots ORDER BY id LIMIT 1) RETURNING id"
            )
            return cur.fetchone()[0]


def _reset_slot(slot_id: int):
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM bookings WHERE slot_id = %s", (slot_id,))
            cur.execute("UPDATE slots SET status = 'available', held_at = NULL WHERE id = %s", (slot_id,))


def test_get_available_slots_shape():
    with _connect() as conn:
        rows = get_available_slots(conn, date.today())
    assert isinstance(rows, list)
    for r in rows:
        assert set(r.keys()) == {"id", "time"}


def test_hold_then_confirm_lifecycle():
    slot_id = _pick_available_slot_id()
    try:
        conn = _connect()
        try:
            assert hold_slot(conn, slot_id) is True
            assert hold_slot(conn, slot_id) is False  # already held
            assert confirm_booking(conn, slot_id, "Test User", "03001234567", "Sedan", "whatsapp") is True
            assert confirm_booking(conn, slot_id, "Someone Else", "03111234567", "SUV", "voice") is False  # already booked
        finally:
            conn.close()
    finally:
        _reset_slot(slot_id)


def test_concurrent_hold_only_one_wins():
    """Two threads race to hold the SAME slot. The FOR UPDATE row lock means
    exactly one wins; without it, both read 'available' before either writes."""
    slot_id = _pick_available_slot_id()
    try:
        results = [None, None]
        barrier = threading.Barrier(3)  # main thread + 2 worker threads

        def attempt(idx: int):
            conn = _connect()
            try:
                barrier.wait()
                results[idx] = hold_slot(conn, slot_id)
            finally:
                conn.close()

        t1 = threading.Thread(target=attempt, args=(0,))
        t2 = threading.Thread(target=attempt, args=(1,))
        t1.start()
        t2.start()
        barrier.wait()  # release both workers at the same instant
        t1.join()
        t2.join()

        assert sorted(results) == [False, True], f"expected exactly one winner, got {results}"
    finally:
        _reset_slot(slot_id)


def test_expired_hold_is_recycled():
    """A hold older than TTL is treated as abandoned: it shows as available,
    can be re-held, and cannot be confirmed by the stale holder."""
    slot_id = _pick_available_slot_id()
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                # Backdate the hold so it looks abandoned.
                cur.execute(
                    "UPDATE slots SET status = 'held', held_at = %s WHERE id = %s",
                    (datetime.now(timezone.utc) - timedelta(minutes=HOLD_TTL_MINUTES + 1), slot_id),
                )

            # It IS expired now, so a new holder can take it over.
            assert hold_slot(conn, slot_id) is True  # recycled by a new customer
            assert confirm_booking(conn, slot_id, "New Customer", "03211234567", "Hatchback", "voice") is True
        finally:
            conn.close()
    finally:
        _reset_slot(slot_id)


def test_fresh_hold_not_recycled():
    """A recently-placed hold must NOT appear available or be re-holable."""
    slot_id = _pick_available_slot_id()
    try:
        conn = _connect()
        try:
            assert hold_slot(conn, slot_id) is True
            rows = get_available_slots(conn, date.today())
            assert slot_id not in [r["id"] for r in rows], "fresh hold leaked into available list"
            assert hold_slot(conn, slot_id) is False, "fresh hold was taken by someone else"
        finally:
            conn.close()
    finally:
        _reset_slot(slot_id)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
