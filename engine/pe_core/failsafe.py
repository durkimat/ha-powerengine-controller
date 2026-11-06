"""The inverter's failsafe, as data the integration can act on when the engine itself cannot.

PowerEngine programs the inverter ahead (timed windows) or keeps a remote-control command going. If the engine stops
while it is in control, the integration (which outlives it) hands the inverter back to Self-Use after 15 minutes of
silence. It does not know the inverter: the engine says what to do, here, in terms of service calls built from the
same adapter that drives the inverter, so it is right for whichever inverter and entities are configured.

A list of calls, each `{"service": "domain/name", "data": {...}}`, in the order to make them, with one
`{"wait_s": 5}` before the update buttons (a button sends the window values already written, so they have to have
landed). The integration skips a number or a select that is already at the value asked.
"""

from __future__ import annotations

from .roles import is_forbidden_control

PAUSE_BEFORE_BUTTONS_S = 5


def calls_for(writes, entities: dict, inverter) -> list[dict]:
    """The service calls for `writes` (control.Write objects), `entities` mapping a write's role to its entity id."""
    steps: list[dict] = []
    buttons: list[str] = []
    for w in writes:
        entity_id = entities.get(w.role)
        if not entity_id or is_forbidden_control(entity_id):
            continue
        if w.kind == "button":
            if entity_id not in buttons:
                buttons.append(entity_id)
            continue
        service, data = inverter.service_for(w)
        steps.append({"service": service, "data": {"entity_id": entity_id, **data}})
    if buttons:
        steps.append({"wait_s": PAUSE_BEFORE_BUTTONS_S})
        steps += [{"service": "button/press", "data": {"entity_id": e}} for e in buttons]
    return steps
