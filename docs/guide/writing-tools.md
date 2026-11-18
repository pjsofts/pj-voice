# Writing a tool server

The prompt asks the model to behave; the tool server makes sure it does. These are the patterns in `pjvoice.booking`, and they apply to any domain (clinics, restaurants, field service, support desks).

## 1. Bind identity on the server
`find_customer` stores `customer_id` in the session state keyed by `session_id`. Later tools (`book_appointment`, `cancel_appointment`) take **no customer argument**; they read it from the session. The model can't attach a booking to the wrong person because it never handles the id.

## 2. Only accept what you offered
`check_availability` records every `slot_id` it returned for this session. `book_appointment` rejects any slot that isn't in that set:

> That slot was not offered on this call. Call check_availability and use a slot_id from its result.

This stops the model from inventing a time that "sounds right".

## 3. Re-check before write
Availability can change between offer and booking. The backend checks again inside a lock, and on conflict the error carries **fresh alternatives**, so the agent can offer a new time in the same turn.

## 4. Make writes idempotent
A second `book_appointment` for the same slot in the same session returns the existing booking. Models do repeat calls, especially after an interruption.

## 5. Ownership checks
Reschedule and cancel verify that the appointment belongs to the session's customer. A caller can't cancel someone else's appointment by guessing an id.

## 6. Facts come from data
Prices and durations are rendered from the catalogue ("$90.00, 90 minutes. Quote these prices exactly."). The model repeats them; it doesn't compute them.

## 7. Write results for the ear
- Local time with weekday: "Thursday 19 November at 9 AM", never ISO strings.
- At most six options, one per start time ("offer two or three").
- Errors that say what to do next.

## Plugging in a real system
Implement the `BookingBackend` protocol (`find_customer`, `create_customer`, `list_services`, `open_slots`, `is_open`, `book`, `list_appointments`, `reschedule`, `cancel`, …) for Cal.com, Google Calendar or your own database, then pass it to `create_app(backend)`. The guards stay the same.
