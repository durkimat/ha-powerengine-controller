"""Layer 1: observe. A filtered battery level, and events from the changes between one tick and the next.

The inverter's level is a whole percent and reads a point lower while charging than while holding. The filter counts
the energy that flows (coulomb counting) and pulls the estimate gently towards the reading (a complementary filter), so
a reading that wobbles by a point does not move it. Everything the rest of the engine reacts to arrives as an `Event`:
nothing downstream polls on a clock to find out that something happened (docs/plans/engine-v2.md, section 4).

Units: levels in percent, power in kW (readings are in W and converted here), times as aware datetimes. The state is
JSON-safe (times as epoch seconds) so the engine can be saved and restored.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from .settings import V2Settings
from .types import Event, Forecast, Observation, StepInput, ValueResult

CHARGING, HOLDING, DISCHARGING = "charging", "holding", "discharging"
FLOW_W = 200                      # below this the battery counts as holding
GAP_POINTS, GAP_MIN = 3.0, 10     # estimate and reading further apart than this for this long: reset to the reading
MAX_TICK_GAP_S = 300              # a longer gap between ticks is not counted as energy
SUN_BAND_KW = 0.05                # net load inside +/- this keeps the sun/short state it had
BMS_MOVE_KW = 0.5
PRICE_SETTLE_S = 90               # a live price change this soon after a boundary is the same change
MIN_FORECAST_KWH = 0.5            # a rest-of-day total below this is too small to judge a change by
REQUIRED = (("battery_soc", "battery level"), ("import_rate", "import rate"))


def power_class(power_w: float | None) -> str:
    if power_w is None or abs(power_w) < FLOW_W:
        return HOLDING
    return CHARGING if power_w < 0 else DISCHARGING


def _same(a, b) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


class Observer:
    def __init__(self, settings: V2Settings, state: dict | None = None):
        self.s = settings
        st = dict(state or {})
        self.level: float | None = st.get("level")
        self.last_ts: float | None = st.get("last_ts")
        self.gap_since: float | None = st.get("gap_since")
        self.gap_max: float = st.get("gap_max", 0.0)
        self.gap_day: str | None = st.get("gap_day")
        self.offsets: dict = dict(st.get("offsets") or {})
        self.missing: dict = dict(st.get("missing") or {})           # name -> epoch when first seen missing
        self.flagged: list = list(st.get("flagged") or [])           # names reported as data_missing
        self.stable: dict = dict(st.get("stable") or {})             # key -> {"v": committed, "c": cand., "t": since}
        self.last_import: float | None = st.get("last_import")
        self.cusum: float = st.get("cusum", 0.0)
        self.drift_fired: bool = st.get("drift_fired", False)
        self.band_since: float | None = st.get("band_since")
        self.band_fired: bool = st.get("band_fired", False)
        self.baseline: dict = dict(st.get("baseline") or {})         # solar points at the last revalue: ts -> kWh
        self.forecast_fired: bool = st.get("forecast_fired", False)
        self.prev: dict = dict(st.get("prev") or {})                 # last tick's simple values
        self._started = False
        self._points: dict = {}                                      # this tick's solar points (ts -> kWh)

    # ---- small helpers --------------------------------------------------------------------------
    def set_offsets(self, offsets: dict | None) -> None:
        """The learned reported-minus-true level per mode (`Learner`), used by the filter."""
        self.offsets = {k: float(v) for k, v in (offsets or {}).items()}

    @property
    def gap_max_today(self) -> float:
        return self.gap_max

    def _settle(self, key: str, value, ts: float, seconds: float, now_event=None):
        """Debounce: `value` becomes the committed one only once it has been the candidate for `seconds`.
        Returns (committed, changed). The first value ever seen is committed at once, without a change."""
        d = self.stable.get(key)
        if d is None:
            self.stable[key] = {"v": value, "c": value, "t": ts}
            return value, False
        if _same(value, d["v"]):
            d["c"], d["t"] = value, ts
            return d["v"], False
        if not _same(value, d["c"]):
            d["c"], d["t"] = value, ts
        if ts - d["t"] >= seconds:
            d["v"] = value
            return value, True
        return d["v"], False

    # ---- the update -----------------------------------------------------------------------------
    def update(self, inp: StepInput, forecast: Forecast | None, vr: ValueResult | None) -> Observation:
        s, now, r, facts = self.s, inp.now, inp.readings, inp.facts
        ts = now.timestamp()
        events: list[Event] = []

        def ev(kind: str, text: str) -> None:
            events.append(Event(now, kind, text))

        first = not self._started
        if first:
            ev("start", "The engine started: working out the first values")

        reported = r.battery_soc
        p_w = r.battery_power
        cls = power_class(p_w)

        # --- missing data (a reading missing for less than stale_after_s is not missing yet) ---
        missing_now = []
        for attr, label in REQUIRED:
            if getattr(r, attr, None) is None:
                self.missing.setdefault(attr, ts)
                if ts - self.missing[attr] >= s.stale_after_s:
                    missing_now.append(label)
            else:
                self.missing.pop(attr, None)
        newly = [m for m in missing_now if m not in self.flagged]
        if newly:
            ev("data_missing", f"No reading for {', '.join(newly)} for {s.stale_after_s // 60 or 1} minutes or more")
        if self.flagged and not missing_now:
            ev("data_back", "The missing readings are back")
        self.flagged = list(missing_now)
        data_ok = not missing_now
        if r.import_rate is not None:
            self.last_import = r.import_rate

        # --- the filtered level ---
        self._filter(ts, reported, p_w, cls, facts, now, first)

        # --- debounced car ---
        raw_car = r.ev_state() == "charging"
        car = self._debounced_car(raw_car, ts, ev, first)

        # --- net load (house and car less solar), kW ---
        net = None
        if r.house_power is not None:
            net = (r.house_power + (r.ev_power or 0.0 if raw_car else 0.0) - (r.solar_power or 0.0)) / 1000.0
        self._sun_events(net, ts, ev)

        # --- the data's own changes ---
        self._data_events(inp, ts, ev, first)

        # --- drift, band and forecast ---
        self._drift(ts, net, car, forecast, ev)
        self._band(ts, vr, now, ev)
        self._forecast_change(inp, ts, ev)

        # --- bms, mode switch, override, settings, learned ---
        self._misc_events(inp, ts, ev, first)

        self._started = True
        self.last_ts = ts
        self.prev["now"] = ts
        return Observation(now=now, level_reported=reported, level_filtered=self.level, net_load_kw=net,
                           car_charging=car, data_ok=data_ok, missing=tuple(missing_now), events=tuple(events))

    # ---- level filter ---------------------------------------------------------------------------
    def _filter(self, ts, reported, p_w, cls, facts, now, first) -> None:
        s = self.s
        dt = (ts - self.last_ts) if self.last_ts is not None else None
        if reported is not None and (self.level is None or first):
            self.level = float(reported)
        elif self.level is not None and dt is not None and 0 < dt <= MAX_TICK_GAP_S and p_w is not None:
            kwh = p_w / 1000.0 * dt / 3600.0                       # + out of the battery
            if kwh < 0:
                delta = -kwh * facts.eta_charge
            else:
                delta = -kwh / max(facts.eta_discharge, 0.01)
            self.level = min(100.0, max(0.0, self.level + delta / max(facts.capacity_kwh, 0.1) * 100.0))
            if reported is not None:
                target = reported - self.offsets.get(cls, 0.0)
                k = 1.0 - (1.0 - s.soc_filter_gain) ** (dt / max(s.sample_s, 1))
                self.level += k * (target - self.level)
                if abs(target - self.level) > GAP_POINTS:
                    self.gap_since = self.gap_since if self.gap_since is not None else ts
                    if ts - self.gap_since >= GAP_MIN * 60:
                        self.level, self.gap_since = float(target), None
                else:
                    self.gap_since = None
        if self.level is not None and reported is not None:
            day = now.date().isoformat()
            if self.gap_day != day:
                self.gap_day, self.gap_max = day, 0.0
            self.gap_max = max(self.gap_max, abs(self.level - reported))

    # ---- debounced car --------------------------------------------------------------------------
    def _debounced_car(self, raw: bool, ts: float, ev, first: bool) -> bool:
        if first or "car" not in self.stable:        # at the start, trust what the car says now
            self.stable["car"] = {"v": raw, "c": raw, "t": ts}
            return raw
        committed, changed = self._settle("car", raw, ts, self.s.car_start_debounce_s if raw
                                          else self.s.car_stop_debounce_s)
        if changed:
            ev("car_start" if committed else "car_stop",
               "The car started charging" if committed else "The car stopped charging")
        return bool(committed)

    # ---- sun and shortfall ----------------------------------------------------------------------
    def _sun_events(self, net: float | None, ts: float, ev) -> None:
        if net is None:
            return
        d = self.stable.get("short")
        cur = d["v"] if d else None
        if cur is None:
            state = net > 0
        elif net > SUN_BAND_KW:
            state = True
        elif net < -SUN_BAND_KW:
            state = False
        else:
            state = cur
        committed, changed = self._settle("short", state, ts, self.s.debounce_s)
        if changed:
            if committed:
                ev("sun_to_short", "The spare sun has turned to a shortfall")
            else:
                ev("short_to_sun", "There is spare sun again")

    # ---- changes in prices, slots and events ----------------------------------------------------
    @staticmethod
    def _windows_sig(ws, now_ts: float) -> list:
        out = []
        for w in ws or []:
            e = w.end.timestamp()
            if e > now_ts:
                out.append([w.start.timestamp(), e, None if w.value is None else round(float(w.value), 5)])
        return sorted(out)

    def _data_events(self, inp: StepInput, ts: float, ev, first: bool) -> None:
        s, r = self.s, inp.readings
        prev_ts = self.prev.get("now", ts)

        # prices published: the set of future rate windows changed (an empty list is a missed reading: ignored)
        sig = self._windows_sig([w for w in r.rates if w.value is not None], ts)
        if sig:
            old = self.stable.get("rates")
            if old is not None:
                old["v"] = [w for w in old["v"] if w[1] > ts]
            committed, changed = self._settle("rates", sig, ts, s.debounce_s)
            if changed:
                ev("prices_published", "New prices have been published")

        # smart slots changed (added, moved, withdrawn, cut short)
        dsig = self._windows_sig(r.dispatches, ts)
        old = self.stable.get("slots")
        if old is not None:
            old["v"] = [w for w in old["v"] if w[1] > ts]
        committed, changed = self._settle("slots", dsig, ts, s.debounce_s)
        if changed:
            ev("slots_changed", "The smart-charge slots have changed")

        # boundaries passed since the last tick: slot start and end, a price change
        if not first and ts > prev_ts:
            for w in r.dispatches:
                if prev_ts < w.start.timestamp() <= ts:
                    ev("slot_start", f"A smart-charge slot started at {_hm(w.start, inp.tz)}")
                if prev_ts < w.end.timestamp() <= ts:
                    ev("slot_end", f"A smart-charge slot ended at {_hm(w.end, inp.tz)}")
            price_text = None
            ordered = sorted((w for w in r.rates if w.value is not None), key=lambda w: w.start)
            for i, w in enumerate(ordered):
                if prev_ts < w.start.timestamp() <= ts:
                    before = ordered[i - 1].value if i else None
                    if before is None or abs(before - w.value) > 1e-6:
                        price_text = f"The import price changes to {w.value * 100:.2f}p"
            live, last = r.import_rate, self.prev.get("rate")
            if (price_text is None and live is not None and last is not None and abs(live - last) > 1e-6
                    and ts - self.prev.get("price_ts", -1e9) > PRICE_SETTLE_S):
                price_text = f"The import price changed to {live * 100:.2f}p"
            if price_text:
                self.prev["price_ts"] = ts
                ev("price", price_text)
        if r.import_rate is not None:
            self.prev["rate"] = r.import_rate

        # a grid event: announced or changed (debounced), started, ended
        was_active = self.prev.get("axle_active")
        if r.axle_active != was_active and was_active is not None:
            ev("event_start" if r.axle_active else "event_end",
               "The grid event started" if r.axle_active else "The grid event ended")
        self.prev["axle_active"] = r.axle_active
        esig = [r.axle_start.timestamp() if r.axle_start else None, r.axle_end.timestamp() if r.axle_end else None]
        committed, changed = self._settle("event", esig, ts, s.debounce_s)
        if changed and any(esig) and not (was_active is not None and r.axle_active != was_active):
            ev("event_changed", "A grid event was announced or changed")

        was_free = self.prev.get("free_active")
        if r.free_active != was_free and was_free is not None:
            ev("free_start" if r.free_active else "free_end",
               "A free-power session started" if r.free_active else "The free-power session ended")
        self.prev["free_active"] = r.free_active
        fsig = [r.free_start.timestamp() if r.free_start else None, r.free_end.timestamp() if r.free_end else None]
        committed, changed = self._settle("free", fsig, ts, s.debounce_s)
        if changed and any(fsig) and not (was_free is not None and r.free_active != was_free):
            ev("event_changed", "A free-power session was announced or changed")

    # ---- drift, band, forecast ------------------------------------------------------------------
    @staticmethod
    def _expected_net_kw(forecast: Forecast | None, now: datetime) -> float | None:
        if forecast is None:
            return None
        for seg in forecast.segments:
            if seg.start <= now < seg.end:
                h = seg.hours or 1e-9
                return (seg.load_kwh.mid - seg.solar_kwh.mid) / h + seg.car_kw
        return None

    def _drift(self, ts, net, car, forecast, ev) -> None:
        prev_ts = self.prev.get("now")
        exp = self._expected_net_kw(forecast, datetime.fromtimestamp(ts, tz=timezone.utc))
        if prev_ts is not None and net is not None and exp is not None and 0 < ts - prev_ts <= MAX_TICK_GAP_S:
            self.cusum += (net - exp) * (ts - prev_ts) / 3600.0
        if not self.drift_fired and abs(self.cusum) >= self.s.drift_kwh:
            self.drift_fired = True
            word = "more" if self.cusum > 0 else "less"
            ev("drift", f"The house is using {abs(self.cusum):.1f} kWh {word} than forecast since the last time "
                        "the values were worked out")

    @property
    def drift_kwh(self) -> float:
        return self.cusum

    def _band(self, ts, vr, now, ev) -> None:
        if vr is None or self.level is None or not vr.path:
            self.band_since = None
            return
        p = vr.path
        try:
            step = float(p["step_min"]) * 60
            i = int((ts - datetime.fromisoformat(p["start"]).timestamp()) // step)
            mid, low, high = p["mid"][i], p["low"][i], p["high"][i]
        except (KeyError, IndexError, ValueError, TypeError):
            self.band_since = None
            return
        lo, hi = min(mid, low, high) - self.s.level_band_pct, max(mid, low, high) + self.s.level_band_pct
        if lo <= self.level <= hi:
            self.band_since, self.band_fired = None, False
            return
        self.band_since = self.band_since if self.band_since is not None else ts
        if not self.band_fired and ts - self.band_since >= self.s.band_exit_min * 60:
            self.band_fired = True
            ev("band_exit", f"The battery has been outside its expected range ({lo:.0f} to {hi:.0f}%) for "
                            f"{self.s.band_exit_min:g} minutes")

    def _rest_of_day(self, inp: StepInput) -> dict:
        now = inp.now
        floor = now.timestamp() - 1800
        day = now.astimezone(inp.tz).date() if inp.tz else now.date()
        out = {}
        for p in inp.solar_points or []:
            t = p.start
            local = t.astimezone(inp.tz).date() if inp.tz else t.date()
            if local == day and t.timestamp() >= floor:
                out[t.timestamp()] = float(p.kwh)
        return out

    def _forecast_change(self, inp: StepInput, ts: float, ev) -> None:
        pts = self._rest_of_day(inp)
        self._points = pts
        if not pts:
            return
        if not self.baseline:
            self.baseline = dict(pts)
            return
        base = {float(k): v for k, v in self.baseline.items()}
        common = [k for k in pts if k in base and k >= ts - 1800]
        old = sum(base[k] for k in common)
        new = sum(pts[k] for k in common)
        if old < MIN_FORECAST_KWH:
            return
        moved = abs(new - old) / old * 100
        stable_moved, changed = self._settle("fc_moved", moved >= self.s.forecast_change_pct, ts, self.s.debounce_s)
        if stable_moved and not self.forecast_fired:
            self.forecast_fired = True
            word = "up" if new > old else "down"
            ev("forecast_update", f"The solar forecast for the rest of today moved {word} by {moved:.0f}% "
                                  f"({old:.1f} to {new:.1f} kWh)")

    # ---- the rest -------------------------------------------------------------------------------
    def _misc_events(self, inp: StepInput, ts: float, ev, first: bool) -> None:
        facts, sit = inp.facts, inp.situation
        cur = [facts.bms_charge_kw if facts.bms_charge_kw is not None else facts.max_charge_kw,
               facts.bms_discharge_kw if facts.bms_discharge_kw is not None else facts.max_discharge_kw]
        old = self.prev.get("bms")
        if old is not None and not first:
            zero_moved = [(a <= 0) != (b <= 0) for a, b in zip(cur, old, strict=True)]
            moved = [abs(a - b) >= BMS_MOVE_KW for a, b in zip(cur, old, strict=True)]
            if any(zero_moved) or any(moved):
                which = "charge" if (zero_moved[0] or moved[0]) else "discharge"
                now_kw = cur[0] if which == "charge" else cur[1]
                ev("bms", f"The battery's {which} limit is now {now_kw:.1f} kW")
                self.prev["bms"] = cur
        else:
            self.prev["bms"] = cur
        active = bool(sit.active)
        if self.prev.get("active") is not None and self.prev["active"] != active:
            ev("mode_switch", "Active mode is on: decisions are sent" if active
               else f"Decisions are no longer sent: {sit.mode_reason or 'Passive or paused'}")
        self.prev["active"] = active
        ov = sit.override.as_dict() if sit.override is not None else None
        if "override" in self.prev and not _same(self.prev["override"], ov):
            ev("override", "The manual override was set or changed" if ov else "The manual override ended")
        self.prev["override"] = ov
        if inp.settings_changed:
            ev("settings", "The engine's settings were saved")
        if inp.learned_changed:
            ev("learned", "Something learned about the house changed")

    # ---- the engine tells us a revalue happened -------------------------------------------------
    def note_revalued(self, now: datetime) -> None:
        """Start the drift sum, the band timer and the forecast baseline again."""
        self.cusum, self.drift_fired = 0.0, False
        self.band_since, self.band_fired = None, False
        self.forecast_fired = False
        if self._points:
            self.baseline = dict(self._points)
        d = self.stable.get("fc_moved")
        if d:
            d.update({"v": False, "c": False, "t": now.timestamp()})

    def state(self) -> dict:
        return {"level": self.level, "last_ts": self.last_ts, "gap_since": self.gap_since, "gap_max": self.gap_max,
                "gap_day": self.gap_day, "offsets": dict(self.offsets), "missing": dict(self.missing),
                "flagged": list(self.flagged), "stable": json.loads(json.dumps(self.stable)),
                "last_import": self.last_import, "cusum": self.cusum, "drift_fired": self.drift_fired,
                "band_since": self.band_since, "band_fired": self.band_fired,
                "baseline": {str(k): v for k, v in self.baseline.items()}, "forecast_fired": self.forecast_fired,
                "prev": json.loads(json.dumps(self.prev))}


def _hm(t: datetime, tz) -> str:
    return (t.astimezone(tz) if tz else t).strftime("%H:%M")

