"""Reference tool server: generic appointment booking with a correctness layer.

Implements the PJ Voice tool protocol (SPEC.md §4) and the guards in SPEC.md §5:
server-side identity, offered-slot guard, re-check before write, idempotency,
prices from data only, and local-time rendering.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Optional

from fastapi import FastAPI, Header, HTTPException, Request

from .backend import Appointment, BookingBackend, SlotTaken, Slot, demo_backend


class ToolError(Exception):
    """Actionable message for the model."""


@dataclass
class SessionState:
    customer_id: Optional[str] = None
    offered: set[str] = field(default_factory=set)
    booked: dict[str, str] = field(default_factory=dict)  # slot_id -> appointment_id


class BookingTools:
    MAX_SLOTS_SPOKEN = 6

    def __init__(self, backend: BookingBackend) -> None:
        self.backend = backend
        self._sessions: dict[str, SessionState] = {}
        self._lock = threading.Lock()
        self.handlers: dict[str, Callable[[SessionState, dict[str, Any]], str]] = {
            "find_customer": self.find_customer,
            "create_customer": self.create_customer,
            "list_services": self.list_services,
            "check_availability": self.check_availability,
            "book_appointment": self.book_appointment,
            "list_appointments": self.list_appointments,
            "reschedule_appointment": self.reschedule_appointment,
            "cancel_appointment": self.cancel_appointment,
        }

    def session(self, session_id: str) -> SessionState:
        with self._lock:
            return self._sessions.setdefault(session_id or "_anon", SessionState())

    def dispatch(self, name: str, session_id: str, args: dict[str, Any]) -> str:
        handler = self.handlers.get(name)
        if handler is None:
            raise ToolError(f"Unknown tool {name}.")
        return handler(self.session(session_id), args or {})

    # ── formatting ─────────────────────────────────────────────────────────
    def _local(self, dt: datetime) -> str:
        loc = dt.astimezone(self.backend.timezone)
        return loc.strftime("%A %-d %B at %-I:%M %p").replace(":00 ", " ")

    def _describe(self, a: Appointment) -> str:
        svc = self.backend.get_service(a.service_id)
        st = self.backend.get_staff(a.staff_id)
        return f"{svc.name if svc else a.service_id} with {st.name if st else a.staff_id} on {self._local(a.start)} (appointment_id {a.id})"

    def _require_customer(self, s: SessionState) -> str:
        if not s.customer_id:
            raise ToolError(
                "No customer is identified on this call yet. Call find_customer first; "
                "if they are new, ask for their name and call create_customer."
            )
        return s.customer_id

    def _own_appointment(self, s: SessionState, appointment_id: str) -> Appointment:
        cid = self._require_customer(s)
        a = self.backend.get_appointment(appointment_id or "")
        if a is None or a.customer_id != cid or a.status != "booked":
            raise ToolError("That appointment is not on this caller's account. Call list_appointments to get the right one.")
        return a

    def _offered_slot(self, s: SessionState, slot_id: str) -> Slot:
        if slot_id not in s.offered:
            raise ToolError(
                "That slot was not offered on this call. Call check_availability and use a slot_id from its result."
            )
        return Slot.parse(slot_id)

    @staticmethod
    def _one_per_time(slots: list[Slot]) -> list[Slot]:
        """Callers pick a time, not a staff member: speak each start time once."""
        seen: set = set()
        out = []
        for sl in slots:
            if sl.start not in seen:
                seen.add(sl.start)
                out.append(sl)
        return out

    def _alternatives(self, s: SessionState, service_id: str, day: date) -> str:
        slots = self._one_per_time(self.backend.open_slots(service_id, day))[: self.MAX_SLOTS_SPOKEN]
        s.offered.update(sl.id for sl in slots)
        if not slots:
            return "There are no other openings that day; offer to check another day."
        return "Other openings: " + "; ".join(self._slot_line(sl) for sl in slots) + "."

    def _slot_line(self, sl: Slot) -> str:
        st = self.backend.get_staff(sl.staff_id)
        return f"{self._local(sl.start)} with {st.name if st else sl.staff_id} (slot_id {sl.id})"

    # ── tools ──────────────────────────────────────────────────────────────
    def find_customer(self, s: SessionState, args: dict[str, Any]) -> str:
        phone = args.get("caller_phone") or args.get("phone") or ""
        c = self.backend.find_customer(phone)
        if c is None:
            return "No customer found for this phone number. Ask for their name to create a profile."
        s.customer_id = c.id
        appts = self.backend.list_appointments(c.id)
        upcoming = f" Upcoming: {'; '.join(self._describe(a) for a in appts)}." if appts else " No upcoming appointments."
        return f"Found customer {c.name}.{upcoming}"

    def create_customer(self, s: SessionState, args: dict[str, Any]) -> str:
        name = (args.get("name") or "").strip()
        phone = args.get("caller_phone") or args.get("phone") or ""
        if len(name) < 2:
            raise ToolError("Ask the caller for their full name, then call create_customer again.")
        if not phone:
            raise ToolError("No phone number is available for this caller. Ask for a callback number.")
        existing = self.backend.find_customer(phone)
        if existing:
            s.customer_id = existing.id
            return f"A profile already exists for this number under {existing.name}; using it."
        c = self.backend.create_customer(name, phone)
        s.customer_id = c.id
        return f"Created a profile for {c.name}."

    def list_services(self, s: SessionState, args: dict[str, Any]) -> str:
        lines = [
            f"{v.name} (service_id {v.id}): {v.minutes} minutes, ${v.price_cents // 100}.{v.price_cents % 100:02d}"
            for v in self.backend.list_services()
        ]
        return "Services: " + "; ".join(lines) + ". Quote these prices exactly."

    def check_availability(self, s: SessionState, args: dict[str, Any]) -> str:
        service_id = args.get("service_id", "")
        if self.backend.get_service(service_id) is None:
            raise ToolError("Unknown service_id. Call list_services and use one of its service_id values.")
        try:
            day = date.fromisoformat(str(args.get("date", "")))
        except ValueError:
            raise ToolError("date must be YYYY-MM-DD.")
        slots = self._one_per_time(self.backend.open_slots(service_id, day))
        if not slots:
            return f"No openings on {day.strftime('%A %-d %B')}. Offer another day."
        shown = slots[: self.MAX_SLOTS_SPOKEN]
        s.offered.update(sl.id for sl in shown)
        more = f" ({len(slots) - len(shown)} more later that day)" if len(slots) > len(shown) else ""
        return "Openings: " + "; ".join(self._slot_line(sl) for sl in shown) + f"{more}. Offer two or three, not all."

    def book_appointment(self, s: SessionState, args: dict[str, Any]) -> str:
        cid = self._require_customer(s)
        slot_id = args.get("slot_id", "")
        if slot_id in s.booked:  # idempotent repeat
            a = self.backend.get_appointment(s.booked[slot_id])
            if a and a.status == "booked":
                return f"Already booked: {self._describe(a)}. Do not book again."
        slot = self._offered_slot(s, slot_id)
        try:
            a = self.backend.book(cid, slot)
        except SlotTaken:
            local_day = slot.start.astimezone(self.backend.timezone).date()
            raise ToolError("That time was just taken. " + self._alternatives(s, slot.service_id, local_day))
        s.booked[slot_id] = a.id
        return f"Booked: {self._describe(a)}. Read this back to the caller."

    def list_appointments(self, s: SessionState, args: dict[str, Any]) -> str:
        cid = self._require_customer(s)
        appts = self.backend.list_appointments(cid)
        if not appts:
            return "This caller has no upcoming appointments."
        return "Upcoming: " + "; ".join(self._describe(a) for a in appts) + "."

    def reschedule_appointment(self, s: SessionState, args: dict[str, Any]) -> str:
        a = self._own_appointment(s, args.get("appointment_id", ""))
        slot = self._offered_slot(s, args.get("slot_id", ""))
        if slot.service_id != a.service_id:
            raise ToolError("That slot is for a different service. Check availability for the same service.")
        try:
            a = self.backend.reschedule(a.id, slot)
        except SlotTaken:
            local_day = slot.start.astimezone(self.backend.timezone).date()
            raise ToolError("That time was just taken. " + self._alternatives(s, slot.service_id, local_day))
        return f"Moved to {self._describe(a)}."

    def cancel_appointment(self, s: SessionState, args: dict[str, Any]) -> str:
        a = self._own_appointment(s, args.get("appointment_id", ""))
        self.backend.cancel(a.id)
        return f"Cancelled: {self._describe(a)}."


def create_app(backend: Optional[BookingBackend] = None, api_key: Optional[str] = None) -> FastAPI:
    tools = BookingTools(backend or demo_backend())
    key = api_key if api_key is not None else os.environ.get("PJVOICE_TOOLS_KEY", "")
    app = FastAPI(title="PJ Voice booking tools")
    app.state.tools = tools

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/tools")
    async def run_tool(request: Request, authorization: str = Header(default="")) -> dict[str, Any]:
        if key and authorization != f"Bearer {key}":
            raise HTTPException(status_code=401, detail="unauthorized")
        body = await request.json()
        call_id = body.get("tool_call_id", "")
        try:
            result = tools.dispatch(body.get("name", ""), body.get("session_id", ""), body.get("arguments") or {})
            return {"results": [{"toolCallId": call_id, "result": result}]}
        except ToolError as e:
            return {"results": [{"toolCallId": call_id, "error": str(e)}]}

    return app
