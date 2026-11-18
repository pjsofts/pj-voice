"""Tutorial tool server: table reservations for a small restaurant.

Run:  uvicorn examples.restaurant.server:app --port 8200
"""

from __future__ import annotations

import itertools
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from fastapi import FastAPI, Request

TABLES = {"t2a": 2, "t2b": 2, "t4a": 4, "t4b": 4, "t6": 6}  # table id -> seats
OPEN, CLOSE, SITTING = time(17, 0), time(22, 0), timedelta(minutes=90)


@dataclass
class Booking:
    id: str
    name: str
    phone: str
    party: int
    table: str
    start: datetime


@dataclass
class Session:
    offered: dict[str, tuple[datetime, int]] = field(default_factory=dict)  # option id -> (time, party)
    booking_id: str | None = None


bookings: dict[str, Booking] = {}
sessions: dict[str, Session] = {}
_ids = itertools.count(1)
_lock = threading.Lock()


class ToolError(Exception):
    pass


def spoken(dt: datetime) -> str:
    return dt.strftime("%A %-d %B at %-I:%M %p").replace(":00 ", " ")


def free_table(start: datetime, party: int, ignore: str | None = None) -> str | None:
    for table, seats in sorted(TABLES.items(), key=lambda kv: kv[1]):  # smallest table that fits
        if seats < party:
            continue
        clash = any(
            b.table == table and b.id != ignore and b.start < start + SITTING and start < b.start + SITTING
            for b in bookings.values()
        )
        if not clash:
            return table
    return None


def check_tables(s: Session, args: dict) -> str:
    party = int(args.get("party_size") or 0)
    if not 1 <= party <= 6:
        raise ToolError("We seat parties of 1 to 6. For larger groups, offer to transfer to the manager.")
    try:
        day = date.fromisoformat(args.get("date", ""))
    except ValueError:
        raise ToolError("date must be YYYY-MM-DD.")
    options = []
    t = datetime.combine(day, OPEN)
    while t + SITTING <= datetime.combine(day, CLOSE) and len(options) < 5:
        if free_table(t, party):
            option_id = f"opt{len(s.offered) + 1}"
            s.offered[option_id] = (t, party)
            options.append(f"{t.strftime('%-I:%M %p')} (option_id {option_id})")
        t += timedelta(minutes=30)
    if not options:
        return f"Fully booked for {party} on {day.strftime('%A %-d %B')}. Offer another day."
    return f"Tables for {party} on {day.strftime('%A %-d %B')}: " + ", ".join(options) + ". Offer two or three."


def reserve(s: Session, args: dict) -> str:
    if s.booking_id:  # idempotent: one reservation per call
        return f"Already reserved: {describe(bookings[s.booking_id])}. Do not book again."
    option = s.offered.get(args.get("option_id", ""))
    if option is None:
        raise ToolError("That option was not offered on this call. Call check_tables first.")
    name = (args.get("name") or "").strip()
    if len(name) < 2:
        raise ToolError("Ask for the name for the reservation.")
    start, party = option
    with _lock:
        table = free_table(start, party)
        if table is None:
            raise ToolError("That time was just taken. Call check_tables again for fresh options.")
        b = Booking(f"R{next(_ids):03d}", name, args.get("caller_phone", ""), party, table, start)
        bookings[b.id] = b
    s.booking_id = b.id
    return f"Reserved: {describe(b)}. Read it back to the caller."


def describe(b: Booking) -> str:
    return f"table for {b.party} under {b.name} on {spoken(b.start)}"


HANDLERS = {"check_tables": check_tables, "reserve": reserve}
app = FastAPI(title="Restaurant tools")


@app.post("/tools")
async def tools(request: Request) -> dict:
    body = await request.json()
    s = sessions.setdefault(body.get("session_id", ""), Session())
    call_id = body.get("tool_call_id", "")
    try:
        handler = HANDLERS[body["name"]]
        return {"results": [{"toolCallId": call_id, "result": handler(s, body.get("arguments") or {})}]}
    except KeyError:
        return {"results": [{"toolCallId": call_id, "error": f"Unknown tool {body.get('name')}."}]}
    except ToolError as e:
        return {"results": [{"toolCallId": call_id, "error": str(e)}]}
