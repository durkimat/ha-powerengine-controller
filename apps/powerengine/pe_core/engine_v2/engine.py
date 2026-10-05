"""`EngineV2`: the facade the app calls once per tick (docs/plans/engine-v2.md; layers in this package's __init__).

    observe -> triggers (revalue? forecast.build + value.solve with the rules' limits_for) -> lines at the filtered
    level and the live prices -> rules.limits_now -> executor.step -> learning -> journal rows -> StepOutput

`step` never raises: a failure is journalled, the previous value result is kept, and the mode goes on (or, if nothing
could be decided, Self-use). Layers 2 and 3 are called through their modules (`forecast.build`, `value.solve`,
`value.lines`) so tests can swap them.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import replace
from datetime import datetime, timezone

from .. import decide as v1d
from . import rules
from .execute import Executor
from .learning import Learner
from .observe import Observer
from .settings import V2Settings
from .triggers import Triggers
from .types import SELF_USE, Event, ModeState, Observation, StepInput, StepOutput, ValueResult

try:                                        # layers 2 and 3 are written separately
    from . import forecast, value
except ImportError:                         # pragma: no cover
    forecast = value = None

JOURNAL_ROWS = 300
RETRY_S = 60
STATE_VERSION = 1


def _iso(t: datetime | None, tz=None) -> str | None:
    if t is None:
        return None
    return (t.astimezone(tz) if tz else t).isoformat(timespec="seconds")


class EngineV2:
    def __init__(self, settings: V2Settings, state: dict | None = None):
        st = dict(state or {})
        self.s = settings
        self.observer = Observer(settings, st.get("observer"))
        self.triggers = Triggers(settings, st.get("triggers"))
        self.executor = Executor(settings, st.get("executor"))
        self.learner = Learner(settings, st.get("learner"))
        self.journal_rows: deque = deque(st.get("journal") or [], maxlen=JOURNAL_ROWS)
        self.vr: ValueResult | None = None                  # not saved: the first tick works the values out again
        self.fc = None
        self.last_value_at: datetime | None = None
        self.last_facts = None
        self.last_output: StepOutput | None = None
        self.tz = None
        self.sending = False
        self.not_sending_reason: str | None = None
        self.last_import_p: float | None = st.get("last_import_p")
        self.last_export_p: float | None = st.get("last_export_p")
        self.errors: dict = dict(st.get("errors") or {})     # day -> count
        self._settings_dirty = False
        self._forced_reason: str | None = None
        self._retry_at: float | None = None

    # ---- settings -------------------------------------------------------------------------------
    def update_settings(self, settings: V2Settings) -> None:
        self.s = settings
        for part in (self.observer, self.triggers, self.executor, self.learner):
            part.s = settings
        self._settings_dirty = True

    # ---- the tick -------------------------------------------------------------------------------
    def step(self, inp: StepInput) -> StepOutput:
        try:
            out = self._step(inp)
        except Exception as exc:                            # never out of the app's cycle
            out = self._fallback(inp, exc)
        self.last_output = out
        return out

    def _step(self, inp: StepInput) -> StepOutput:
        s, now, r, facts = self.s, inp.now, inp.readings, inp.facts
        self.tz = inp.tz
        self.triggers.tz = inp.tz
        self.executor.set_tz(inp.tz)
        self.last_facts = facts
        self.sending = bool(inp.situation.active)
        self.not_sending_reason = None if self.sending else (inp.situation.mode_reason or "Not in Active mode")
        rows: list[dict] = []

        self.observer.set_offsets(self.learner.soc_offset())
        obs = self.observer.update(inp, self.fc, self.vr)
        events = list(obs.events)
        if self._settings_dirty and not any(e.kind == "settings" for e in events):
            events.append(Event(now, "settings", "The engine's settings were saved"))
        self._settings_dirty = False

        # --- revalue? ---
        level = obs.level_filtered if obs.level_filtered is not None else obs.level_reported
        reason = self.triggers.due(now, tuple(events), self.last_value_at)
        if reason is None and self._forced_reason:
            reason = self._forced_reason
        self._forced_reason = None
        if reason is None and self.vr is None and (self._retry_at is None or now.timestamp() >= self._retry_at):
            reason = self.triggers.cause("start" if self._retry_at is None else "retry")
        revalued, calc_s = False, None
        if reason is not None:
            if level is None:
                self._forced_reason = reason                # no battery level to start from yet: try next tick
            else:
                revalued, calc_s = self._revalue(inp, level, reason, rows)

        # --- live comparison and limits ---
        imp = r.import_rate * 100 if r.import_rate is not None else self.last_import_p
        exp = r.export_rate * 100 if r.export_rate is not None else self.last_export_p
        if r.import_rate is not None:
            self.last_import_p = imp
        if r.export_rate is not None:
            self.last_export_p = exp
        ln = None
        if self.vr is not None and level is not None and imp is not None:
            try:
                ln = value.lines(self.vr, now, level, imp, exp if exp is not None else 0.0, facts, s)
            except Exception as exc:
                rows.append(self._error_row(now, "lines", exc))
        seg = None
        if self.vr is not None:
            seg = next((g for g in self.vr.forecast.segments if g.start <= now < g.end), None)
        lim = rules.limits_now(inp.situation, obs, seg, facts, s, r, reserve_latched=self.executor.reserve_latched)

        # --- the mode ---
        mode, decision, new_events, changed = self.executor.step(now, obs, lim, ln, self.vr, tuple(events), facts)
        events.extend(new_events)
        if new_events:                                      # a deadline passed: work the values out again
            more = self.triggers.due(now, new_events, self.last_value_at)
            if more:
                self._forced_reason = more

        # --- learning ---
        learned_changed = self.learner.observe(now, obs, r, self.fc, inp.tz)
        self.learner.note_comfort(now, level, self.vr.comfort_given_up_p if self.vr else None, inp.tz)
        if learned_changed:
            ev = Event(now, "learned", "Something learned about the house moved enough to work the values out again")
            events.append(ev)
            more = self.triggers.due(now, (ev,), self.last_value_at)
            if more:
                self._forced_reason = more

        if changed:
            rows.append(self._mode_row(now, mode, obs, ln, level, imp, exp))
        self.triggers.note(now, tuple(events), revalued, changed, calc_s, flip_flop=self.executor.flip_flop_now)
        for row in rows:
            self.journal_rows.append(row)
        return StepOutput(decision=decision, mode=mode, value=self.vr, observation=obs, events=tuple(events),
                          revalued=revalued, mode_changed=changed, journal=tuple(rows))

    # ---- the revalue ----------------------------------------------------------------------------
    def _revalue(self, inp: StepInput, level: float, reason: str, rows: list) -> tuple[bool, float | None]:
        now = inp.now
        t0 = time.perf_counter()
        try:
            if forecast is None or value is None:
                raise RuntimeError("the forecast and value layers are not available")
            fc = forecast.build(inp, self.s, self.learner.learned())
            house_ev = inp.situation.house_load_includes_ev

            def limits_for(seg):
                return rules.limits_for(seg, inp.facts, self.s, house_load_includes_ev=house_ev)

            vr = value.solve(fc, level, inp.facts, self.s, limits_for, now, reason, inp.tz)
        except Exception as exc:
            rows.append(self._error_row(now, "revalue", exc, because=reason))
            self._retry_at = now.timestamp() + RETRY_S
            return False, None
        calc = time.perf_counter() - t0
        if not getattr(vr, "calc_s", 0):
            vr = replace(vr, calc_s=round(calc, 2))
        self.fc, self.vr, self.last_value_at, self._retry_at = fc, vr, now, None
        self.observer.note_revalued(now)
        rows.append({"at": _iso(now), "kind": "revalue", "because": reason, "calc_s": round(vr.calc_s or calc, 2),
                     "level": round(level, 1), "modes": [it.mode for it in vr.timeline][:8],
                     "cost_expected": round(vr.cost_expected_p / 100, 2),
                     "cost_selfuse": round(vr.cost_selfuse_p / 100, 2)})
        return True, vr.calc_s or calc

    def _error_row(self, now: datetime, where: str, exc: Exception, because: str | None = None) -> dict:
        day = now.date().isoformat()
        self.errors[day] = self.errors.get(day, 0) + 1
        for d in sorted(self.errors)[:-7]:
            del self.errors[d]
        row = {"at": _iso(now), "kind": "error", "where": where, "error": f"{type(exc).__name__}: {exc}"[:200]}
        if because:
            row["because"] = because
        return row

    def _mode_row(self, now, mode: ModeState, obs: Observation, ln, level, imp, exp) -> dict:
        row = {"at": _iso(now), "kind": "mode", "event": mode.chosen_by, "to": mode.mode, "rule": mode.rule,
               "why": mode.why[:300], "level": None if level is None else round(level, 1),
               "import_p": None if imp is None else round(imp, 2), "export_p": None if exp is None else round(exp, 2)}
        prev = self.executor.changes[-1] if self.executor.changes else None
        row["from"] = prev[1] if prev and prev[2] == mode.mode and prev[0] == now.timestamp() else None
        if ln is not None:
            row.update(value_p=round(ln.value_p, 2), buy_line_p=round(ln.buy_line_p, 2),
                       sell_line_p=round(ln.sell_line_p, 2), use_line_p=round(ln.use_line_p, 2))
        return row

    # ---- when something went wrong with the whole step ------------------------------------------
    def _fallback(self, inp: StepInput, exc: Exception) -> StepOutput:
        now = inp.now
        row = self._error_row(now, "step", exc)
        self.journal_rows.append(row)
        r = inp.readings
        obs = Observation(now=now, level_reported=getattr(r, "battery_soc", None),
                          level_filtered=self.observer.level, net_load_kw=None, car_charging=False, data_ok=False)
        decision = self.executor.last_decision or v1d.Decision(
            v1d.SELF_USE, "v2_error", "engine v2 hit a problem, so the battery stays on self-use",
            details={"engine": "v2", "mode": SELF_USE})
        try:
            mode = self.executor.mode_state()
        except Exception:
            mode = ModeState(SELF_USE, now, "Engine v2 hit a problem", "v2_error", "error")
        return StepOutput(decision=decision, mode=mode, value=self.vr, observation=obs, events=(),
                          revalued=False, mode_changed=False, journal=(row,))

    # ---- state and health -----------------------------------------------------------------------
    def journal(self) -> list[dict]:
        return list(self.journal_rows)

    def state(self) -> dict:
        return {"version": STATE_VERSION, "observer": self.observer.state(), "triggers": self.triggers.state(),
                "executor": self.executor.state(), "learner": self.learner.state(), "journal": list(self.journal_rows),
                "last_import_p": self.last_import_p, "last_export_p": self.last_export_p, "errors": dict(self.errors),
                "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    def health(self, now: datetime) -> dict:
        age = None if self.last_value_at is None else round((now - self.last_value_at).total_seconds() / 60, 1)
        t = self.triggers.health(now)
        return {"engine": "v2", "mode": self.executor.mode, "since": _iso(
                    datetime.fromtimestamp(self.executor.since, tz=timezone.utc) if self.executor.since else None),
                "values_age_min": age, "values_at": _iso(self.last_value_at),
                "values_because": self.vr.because if self.vr else None,
                "calc_s": self.vr.calc_s if self.vr else None, "today": t,
                "learning_days": self.learner.days, "errors_today": self.errors.get(now.date().isoformat(), 0),
                "sending": self.sending, "not_sending_reason": self.not_sending_reason,
                "filter_gap_max_today": round(self.observer.gap_max_today, 2)}
