"""Booking backend interface and an in-memory implementation (SPEC.md §5).

Real systems (Cal.com, Google Calendar, a salon or clinic system) implement
``BookingBackend``; the tool server and its correctness layer stay the same.
"""

from __future__ import annotations

import itertools
import re
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional, Protocol
from zoneinfo import ZoneInfo


class SlotTaken(Exception):
    pass


@dataclass
class Service:
    id: str
    name: str
    minutes: int
    price_cents: int


@dataclass
class Staff:
    id: str
    name: str
    service_ids: list[str]


@dataclass
class Customer:
    id: str
    name: str
    phone: str


@dataclass
class Appointment:
    id: str
    customer_id: str
    service_id: str
    staff_id: str
    start: datetime  # UTC
    end: datetime  # UTC
    status: str = "booked"  # booked | cancelled


@dataclass(frozen=True)
class Slot:
    service_id: str
    staff_id: str
    start: datetime  # UTC

    @property
    def id(self) -> str:
        return f"{self.service_id}|{self.staff_id}|{self.start.strftime('%Y%m%dT%H%MZ')}"

    @classmethod
    def parse(cls, slot_id: str) -> "Slot":
        try:
            service_id, staff_id, ts = slot_id.split("|")
            start = datetime.strptime(ts, "%Y%m%dT%H%MZ").replace(tzinfo=timezone.utc)
        except ValueError as e:
            raise ValueError(f"invalid slot id {slot_id!r}") from e
        return cls(service_id, staff_id, start)


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    return f"+{digits}" if digits else ""


class BookingBackend(Protocol):
    timezone: ZoneInfo

    def find_customer(self, phone: str) -> Optional[Customer]: ...
    def create_customer(self, name: str, phone: str) -> Customer: ...
    def get_customer(self, customer_id: str) -> Optional[Customer]: ...
    def list_services(self) -> list[Service]: ...
    def get_service(self, service_id: str) -> Optional[Service]: ...
    def get_staff(self, staff_id: str) -> Optional[Staff]: ...
    def open_slots(self, service_id: str, day: date) -> list[Slot]: ...
    def is_open(self, slot: Slot) -> bool: ...
    def book(self, customer_id: str, slot: Slot) -> Appointment: ...
    def list_appointments(self, customer_id: str) -> list[Appointment]: ...
    def get_appointment(self, appointment_id: str) -> Optional[Appointment]: ...
    def reschedule(self, appointment_id: str, slot: Slot) -> Appointment: ...
    def cancel(self, appointment_id: str) -> Appointment: ...


@dataclass
class BusinessHours:
    open: time = time(9, 0)
    close: time = time(17, 0)
    days: tuple[int, ...] = (0, 1, 2, 3, 4, 5)  # Mon–Sat
    step_minutes: int = 30


class MemoryBackend:
    """Thread-safe in-memory backend. Good for demos, tests and evals."""

    def __init__(
        self,
        services: list[Service],
        staff: list[Staff],
        tz: str = "UTC",
        hours: Optional[BusinessHours] = None,
        now: Optional[callable] = None,
    ) -> None:
        self.timezone = ZoneInfo(tz)
        self.hours = hours or BusinessHours()
        self._services = {s.id: s for s in services}
        self._staff = {s.id: s for s in staff}
        self._customers: dict[str, Customer] = {}
        self._appts: dict[str, Appointment] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _id(self, prefix: str) -> str:
        return f"{prefix}{next(self._ids):04d}"

    # customers
    def find_customer(self, phone: str) -> Optional[Customer]:
        p = normalize_phone(phone)
        return next((c for c in self._customers.values() if c.phone == p), None) if p else None

    def create_customer(self, name: str, phone: str) -> Customer:
        with self._lock:
            c = Customer(self._id("C"), name.strip(), normalize_phone(phone))
            self._customers[c.id] = c
            return c

    def get_customer(self, customer_id: str) -> Optional[Customer]:
        return self._customers.get(customer_id)

    # catalogue
    def list_services(self) -> list[Service]:
        return list(self._services.values())

    def get_service(self, service_id: str) -> Optional[Service]:
        return self._services.get(service_id)

    def get_staff(self, staff_id: str) -> Optional[Staff]:
        return self._staff.get(staff_id)

    # availability
    def _busy(self, staff_id: str, start: datetime, end: datetime, ignore: Optional[str] = None) -> bool:
        return any(
            a.staff_id == staff_id and a.status == "booked" and a.id != ignore and a.start < end and start < a.end
            for a in self._appts.values()
        )

    def _candidate_starts(self, day: date, minutes: int) -> list[datetime]:
        if day.weekday() not in self.hours.days:
            return []
        tz = self.timezone
        cur = datetime.combine(day, self.hours.open, tz)
        close = datetime.combine(day, self.hours.close, tz)
        now = self._now()
        out = []
        while cur + timedelta(minutes=minutes) <= close:
            if cur.astimezone(timezone.utc) > now:
                out.append(cur.astimezone(timezone.utc))
            cur += timedelta(minutes=self.hours.step_minutes)
        return out

    def open_slots(self, service_id: str, day: date) -> list[Slot]:
        svc = self._services.get(service_id)
        if svc is None:
            return []
        slots = []
        for start in self._candidate_starts(day, svc.minutes):
            end = start + timedelta(minutes=svc.minutes)
            for st in self._staff.values():
                if service_id in st.service_ids and not self._busy(st.id, start, end):
                    slots.append(Slot(service_id, st.id, start))
        return slots

    def is_open(self, slot: Slot, ignore: Optional[str] = None) -> bool:
        svc = self._services.get(slot.service_id)
        st = self._staff.get(slot.staff_id)
        if svc is None or st is None or slot.service_id not in st.service_ids:
            return False
        local_day = slot.start.astimezone(self.timezone).date()
        if slot.start not in self._candidate_starts(local_day, svc.minutes):
            return False
        return not self._busy(st.id, slot.start, slot.start + timedelta(minutes=svc.minutes), ignore)

    # appointments
    def book(self, customer_id: str, slot: Slot) -> Appointment:
        with self._lock:
            if not self.is_open(slot):
                raise SlotTaken(slot.id)
            svc = self._services[slot.service_id]
            a = Appointment(
                self._id("A"), customer_id, slot.service_id, slot.staff_id,
                slot.start, slot.start + timedelta(minutes=svc.minutes),
            )
            self._appts[a.id] = a
            return a

    def list_appointments(self, customer_id: str) -> list[Appointment]:
        now = self._now()
        return sorted(
            (a for a in self._appts.values() if a.customer_id == customer_id and a.status == "booked" and a.end > now),
            key=lambda a: a.start,
        )

    def all_appointments(self) -> list[Appointment]:
        return list(self._appts.values())

    def get_appointment(self, appointment_id: str) -> Optional[Appointment]:
        return self._appts.get(appointment_id)

    def reschedule(self, appointment_id: str, slot: Slot) -> Appointment:
        with self._lock:
            a = self._appts[appointment_id]
            if slot.service_id != a.service_id or not self.is_open(slot, ignore=a.id):
                raise SlotTaken(slot.id)
            svc = self._services[slot.service_id]
            a.staff_id, a.start, a.end = slot.staff_id, slot.start, slot.start + timedelta(minutes=svc.minutes)
            return a

    def cancel(self, appointment_id: str) -> Appointment:
        with self._lock:
            a = self._appts[appointment_id]
            a.status = "cancelled"
            return a


def demo_backend(tz: str = "America/New_York", now=None) -> MemoryBackend:
    """A small generic salon used by the example config, tests and evals."""
    b = MemoryBackend(
        services=[
            Service("haircut", "Haircut", 30, 3500),
            Service("color", "Hair colour", 90, 9000),
            Service("beard", "Beard trim", 30, 2000),
        ],
        staff=[
            Staff("sam", "Sam", ["haircut", "beard"]),
            Staff("alex", "Alex", ["haircut", "color"]),
        ],
        tz=tz,
        now=now,
    )
    b.create_customer("Jordan Lee", "+15550111")
    return b
