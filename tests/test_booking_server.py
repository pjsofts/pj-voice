from datetime import date

from pjvoice.booking.app import BookingTools, ToolError
from pjvoice.booking.backend import Slot

import pytest


def test_reschedule_and_cancel_own_appointments_only(backend):
    tools = BookingTools(backend)
    a = tools.session("a")
    tools.find_customer(a, {"caller_phone": "+15550111"})
    tools.check_availability(a, {"service_id": "beard", "date": "2026-11-18"})
    slot = sorted(a.offered)[0]
    tools.book_appointment(a, {"slot_id": slot})
    appt = backend.list_appointments(a.customer_id)[0]

    # someone else on another call can't touch it
    b = tools.session("b")
    tools.create_customer(b, {"name": "Other Person", "caller_phone": "+15550999"})
    with pytest.raises(ToolError, match="not on this caller's account"):
        tools.cancel_appointment(b, {"appointment_id": appt.id})

    tools.check_availability(a, {"service_id": "beard", "date": "2026-11-19"})
    new_slot = next(s for s in a.offered if "20261119" in s)
    assert "Moved to" in tools.reschedule_appointment(a, {"appointment_id": appt.id, "slot_id": new_slot})
    assert "Cancelled" in tools.cancel_appointment(a, {"appointment_id": appt.id})
    assert backend.list_appointments(a.customer_id) == []


def test_slot_taken_between_offer_and_book_gives_alternatives(backend):
    tools = BookingTools(backend)
    a, b = tools.session("a"), tools.session("b")
    for s, phone in ((a, "+15550111"), (b, "+15550222")):
        if s is b:
            tools.create_customer(b, {"name": "Bee Bee", "caller_phone": phone})
        else:
            tools.find_customer(a, {"caller_phone": phone})
        tools.check_availability(s, {"service_id": "color", "date": "2026-11-18"})
    slot = next(x for x in sorted(a.offered) if "|alex|" in x)
    tools.book_appointment(a, {"slot_id": slot})
    with pytest.raises(ToolError, match="just taken. Other openings"):
        tools.book_appointment(b, {"slot_id": slot})


def test_prices_and_closed_days(backend):
    tools = BookingTools(backend)
    s = tools.session("x")
    assert "$90.00" in tools.list_services(s, {})
    assert "No openings" in tools.check_availability(s, {"service_id": "haircut", "date": "2026-11-22"})  # Sunday
    with pytest.raises(ToolError):
        tools.check_availability(s, {"service_id": "nails", "date": "2026-11-18"})


def test_slot_roundtrip():
    s = Slot.parse("haircut|sam|20261117T1400Z")
    assert s.id == "haircut|sam|20261117T1400Z"
