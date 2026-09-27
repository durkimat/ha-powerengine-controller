"""Supervised test writes: exercise the control path on the real inverter before Active mode ships.

Started from the config card (admin only, since HA only lets admins fire events), with someone watching.
A test writes the settings for one action (hold, charge, discharge or self-use) for at most MAX_MINUTES,
reads them back, records what the battery did, then hands the inverter back to Self-Use and reads that
back too. Refused unless the handover guards show nothing else is controlling the inverter.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .decide import FORCE_DISCHARGE, GRID_CHARGE, HOLD, SELF_USE, Decision
from .rctest import RC_TESTS

ACTIONS = {"hold": HOLD, "charge": GRID_CHARGE, "discharge": FORCE_DISCHARGE, "self_use": SELF_USE}
MAX_MINUTES = 10
MAX_MINUTES_RC = 15
MAX_MINUTES_FAILSAFE = 35            # the RC failsafe test watches for the inverter's own timeout (up to 30 min)
MAX_POWER_W = 6000


def validate(data: dict, guards: list[str], missing: list[str], running: bool) -> tuple[dict | None, str | None]:
    """(request, None) or (None, why it's refused)."""
    if running:
        return None, "a test is already running"
    action = str(data.get("action", ""))
    if action not in ACTIONS and action not in RC_TESTS:
        return None, f"unknown action '{action}'"
    if data.get("confirm") is not True:
        return None, "not confirmed"
    if missing:
        return None, "control outputs not mapped: " + ", ".join(missing[:4])
    if guards:
        return None, "handover guards not safe: " + "; ".join(guards)
    try:
        minutes = int(data.get("minutes", 5))
    except (TypeError, ValueError):
        return None, "minutes must be a whole number"
    top = MAX_MINUTES_FAILSAFE if action == "rc_failsafe" else MAX_MINUTES_RC if action in RC_TESTS else MAX_MINUTES
    low = 4 if action == "rc_failsafe" else 1
    if not low <= minutes <= top:
        return None, f"minutes must be {low} to {top}"
    power = data.get("power_w")
    if power is not None:
        try:
            power = float(power)
        except (TypeError, ValueError):
            return None, "power_w must be a number"
        if not (0 <= power if action == "rc_hold" else 0 < power) or power > MAX_POWER_W:
            return None, f"power_w must be up to {MAX_POWER_W}"
    return {"action": action, "minutes": minutes, "power_w": power}, None


def decision(req: dict) -> Decision:
    return Decision(ACTIONS[req["action"]], "test", "supervised test", power_w=req.get("power_w"))


def end_time(now_local: datetime, minutes: int) -> datetime:
    """Window end for the test: a couple of minutes past the test so the inverter doesn't stop early;
    never past midnight."""
    last = (now_local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(minutes=1)
    return min(now_local + timedelta(minutes=minutes + 2), last)


class TestRun:
    """What one test did, for the diagnostic sensor and the log."""

    def __init__(self, req: dict, started: datetime):
        self.req = req
        self.started = started
        self.status = "running"          # running -> reverting -> passed | failed | stopped
        self.steps: list[dict] = []
        self.problems: list[str] = []
        self.verdict: str | None = None     # RC tests: worked / no effect / reverted / ...
        self.explanation = ""

    def step(self, when: datetime, what: str, **details) -> None:
        self.steps.append({"time": when.isoformat(timespec="seconds"), "what": what, **details})

    def finish(self, status: str | None = None) -> None:
        self.status = status or ("failed" if self.problems else "passed")

    @property
    def done(self) -> bool:
        return self.status in ("passed", "failed", "stopped", "refused")

    def as_dict(self) -> dict:
        return {"action": self.req.get("action"), "minutes": self.req.get("minutes"),
                "power_w": self.req.get("power_w"), "started": self.started.isoformat(timespec="seconds"),
                "verdict": self.verdict, "explanation": self.explanation,
                "problems": self.problems, "steps": self.steps[-40:]}
