# shared/seed_slots.py
import os
import psycopg2
from datetime import date, time, timedelta
from dotenv import load_dotenv

# Why a separate seed script: slots are business config (business hours),
# not something the app should compute live — this keeps that config in one
# obvious place both agents' capacity depends on.

load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]


def generate_week_of_slots(start_date: date):
    # 6 service days, Mon-Sat (Sunday closed), starting at start_date.
    # For each service day, one slot per hour from 9am to 5pm start times
    # (last wash starts 5pm, ends 6pm).
    service_days = 0
    d = start_date
    while service_days < 6:
        if d.weekday() != 6:  # weekday() == 6 means Sunday -> closed
            service_days += 1
            for h in range(9, 18):  # 9:00 .. 17:00 inclusive
                yield d, time(hour=h)
        d += timedelta(days=1)


def main():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    slots = list(generate_week_of_slots(date.today()))
    print(f"Generating {len(slots)} slots starting {slots[0][0]} (first slot), {slots[-1][0]} (last slot)")

    inserted = 0
    for slot_date, slot_time in slots:
        cur.execute(
            "INSERT INTO slots (slot_date, slot_time) VALUES (%s, %s) "
            "ON CONFLICT (slot_date, slot_time) DO NOTHING",
            (slot_date, slot_time),
        )
        inserted += cur.rowcount  # 1 if newly inserted, 0 if it already existed

    conn.commit()
    conn.close()
    print(f"Inserted {inserted} new rows (idempotent: safe to re-run).")


if __name__ == "__main__":
    main()
