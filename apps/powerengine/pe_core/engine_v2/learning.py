"""Learning for engine v2: scenario weights, solar bias, load spread and the battery level's offset per mode.

(docs/plans/engine-v2.md, section 5.1 and 4.) Each only learns when its setting is on.

* Scenario weights. After each half-hour, which of the forecast's low / middle / high figures did the actual energy come
  closest to? Counted with recency weighting (half-life `scenario_half_life_days`), per part of the day for the sun
  (daylight only) and over the whole day for the house, from a prior: the starting weights, worth `scenario_prior_days`
  days of observations. So nothing moves much on the first day. Each weight stays within 0.05 to 0.8.
* Solar bias per local hour: actual over the (un-biased) forecast, over the last 14 days, within 0.5 to 1.5.
* Load spread per half-hour of the day: the 20th and 80th percentile of actual house load minus the forecast's middle.
* Level offset per mode: when the mode changes with little energy flowing, the jump in the reported level tells how far
  the reading is off while charging, holding and discharging. Defined as reported minus true (holding is the
  reference, 0); the observer pulls towards `reported - offset`.

`observe` returns True only when what the forecast and the filter use has moved enough to be worth working the values
out again. The state is JSON-safe.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .observe import HOLDING, power_class
from .settings import V2Settings
from .types import Forecast, Observation

PARTS = ("morning", "midday", "afternoon")
OBS_PER_DAY = {"morning": 6, "midday": 8, "afternoon": 8, "load": 48}   # half-hours that count in a day, for the prior
W_MIN, W_MAX = 0.05, 0.8
BIAS_DAYS = 14
BIAS_MIN, BIAS_MAX = 0.5, 1.5
BIAS_MIN_RAW_KWH = 0.2
SPREAD_SAMPLES = 30
SPREAD_MIN = 5
MIN_COVER_S = 24 * 60                    # a half-hour counts when at least this much of it was measured
DAYLIGHT_KWH = 0.05                      # a half-hour whose middle solar forecast is below this is not daylight
OFFSET_GAIN, OFFSET_MAX = 0.2, 3.0
OFFSET_WINDOW_S, OFFSET_ENERGY_KWH = 180, 0.1
HALF_S = 1800
MOVED_WEIGHT, MOVED_BIAS, MOVED_OFFSET = 0.05, 0.05, 0.3
MIN_FLAG_S = 3600                        # a change is reported at most this often
MODES = ("charging", "holding", "discharging")


def part_of_day(hour: int) -> str:
    return "morning" if hour < 10 else "midday" if hour < 14 else "afternoon"


def clamp_weights(w: list[float]) -> list[float]:
    """Weights summing to 1 with each within W_MIN..W_MAX (the ones over the top are cut first, the rest share what
    is left in proportion, then the ones under the bottom are lifted)."""
    w = [max(0.0, x) for x in w]
    t = sum(w) or 1.0
    w = [x / t for x in w] if sum(w) else [1.0 / len(w)] * len(w)
    fixed: dict[int, float] = {}
    for _ in range(2 * len(w)):
        free = [i for i in range(len(w)) if i not in fixed]
        over = [i for i in free if w[i] > W_MAX + 1e-12]
        under = [i for i in free if w[i] < W_MIN - 1e-12]
        if not over and not under:
            break
        for i in (over or under):
            fixed[i] = W_MAX if over else W_MIN
        free = [i for i in range(len(w)) if i not in fixed]
        rest = 1.0 - sum(fixed.values())
        base = sum(w[i] for i in free)
        for i in free:
            w[i] = w[i] / base * rest if base > 0 else rest / len(free)
        for i, v in fixed.items():
            w[i] = v
    return w


def _percentile(xs: list[float], p: float) -> float:
    ys = sorted(xs)
    k = (len(ys) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(ys) - 1)
    return ys[lo] + (ys[hi] - ys[lo]) * (k - lo)


class Learner:
    def __init__(self, settings: V2Settings, state: dict | None = None):
        self.s = settings
        st = dict(state or {})
        self.solar: dict = {p: dict(v) for p, v in (st.get("solar") or {}).items()}
        self.load: dict = dict(st.get("load") or {})
        self.days: int = st.get("days", 0)
        self.last_day: str | None = st.get("last_day")
        self.by_date: dict = {d: {h: list(v) for h, v in hs.items()} for d, hs in (st.get("by_date") or {}).items()}
        self.resid: dict = {k: list(v) for k, v in (st.get("resid") or {}).items()}
        self.offsets: dict = {m: float((st.get("offsets") or {}).get(m, 0.0)) for m in MODES}
        self.acc: dict | None = st.get("acc")
        self.pend: dict | None = st.get("pend")
        self.last_ts: float | None = st.get("last_ts")
        self.last_cls: str | None = st.get("last_cls")
        self.last_level: float | None = st.get("last_level")
        self.comfort: list = list(st.get("comfort") or [])
        self.comfort_ts: float | None = st.get("comfort_ts")
        self.reported: dict = dict(st.get("reported") or self._view())
        self._dirty = False
        self.last_flag: float | None = st.get("last_flag")

    # ---- weights --------------------------------------------------------------------------------
    def _weights(self, counts: dict | None, start: tuple, per_day: int) -> list[float]:
        if not self.s.learn_scenario_weights:
            return clamp_weights(list(start))
        c = (counts or {}).get("c") or [0.0, 0.0, 0.0]
        prior = self.s.scenario_prior_days * per_day
        return clamp_weights([start[i] * prior + c[i] for i in range(3)])

    def solar_weights(self, part: str) -> list[float]:
        return self._weights(self.solar.get(part), self.s.solar_weights, OBS_PER_DAY[part])

    def load_weights(self) -> list[float]:
        return self._weights(self.load, self.s.load_weights, OBS_PER_DAY["load"])

    def solar_bias(self) -> dict:
        """{hour (str): ratio}, the hours with enough data; empty when learning it is off."""
        if not self.s.learn_solar_bias:
            return {}
        dates = sorted(self.by_date)[-BIAS_DAYS:]
        sums: dict[str, list[float]] = {}
        for d in dates:
            for h, (act, raw) in self.by_date[d].items():
                t = sums.setdefault(h, [0.0, 0.0])
                t[0] += act
                t[1] += raw
        return {h: round(min(BIAS_MAX, max(BIAS_MIN, a / r)), 3) for h, (a, r) in sums.items() if r >= BIAS_MIN_RAW_KWH}

    def load_spread(self) -> dict:
        """{half-hour of day (str): [p20, p80]} of actual minus the forecast's middle, kWh per half-hour."""
        return {k: [round(_percentile(v, 0.2), 4), round(_percentile(v, 0.8), 4)]
                for k, v in self.resid.items() if len(v) >= SPREAD_MIN}

    def soc_offset(self) -> dict:
        return {m: round(self.offsets[m], 2) for m in MODES} if self.s.learn_soc_offset else {m: 0.0 for m in MODES}

    # ---- what the forecast reads and the sensor shows -------------------------------------------
    def learned(self) -> dict:
        """The view `forecast.build` takes (`learned`): weights by part of day, bias per hour, load spread per
        half-hour. The hour and half-hour keys are there both as numbers and as text."""
        bias = self.solar_bias()
        spread = self.load_spread()
        both = lambda d: {**d, **{int(k): v for k, v in d.items()}}       # noqa: E731
        sw = {p: self.solar_weights(p) for p in PARTS}
        return {"weights": {"solar": sw, "load": self.load_weights()}, "solar_weights": sw,
                "load_weights": self.load_weights(), "solar_bias": both(bias), "load_spread": both(spread),
                "soc_offset": self.soc_offset(), "days": self.days}

    def _view(self) -> dict:
        return {"solar": {p: self.solar_weights(p) for p in PARTS}, "load": self.load_weights(),
                "bias": self.solar_bias(), "offsets": self.soc_offset()}

    def diag(self) -> dict:
        """The attributes of sensor.pe_diag_v2 (without the observer's gap and the comfort rows)."""
        r2 = lambda xs: [round(x, 2) for x in xs]                         # noqa: E731
        return {"weights": {"solar": {p: r2(self.solar_weights(p)) for p in PARTS}, "load": r2(self.load_weights()),
                            "days": self.days, "start": {"solar": r2(self.s.solar_weights),
                                                         "load": r2(self.s.load_weights)}},
                "solar_bias": {"days": min(len(self.by_date), BIAS_DAYS), "by_hour": self.solar_bias()},
                "soc_offset": self.soc_offset()}

    # ---- observing ------------------------------------------------------------------------------
    def observe(self, now: datetime, obs: Observation, readings, forecast: Forecast | None, tz) -> bool:
        ts = now.timestamp()
        dt = ts - self.last_ts if self.last_ts is not None else None
        if dt is not None and 0 < dt <= 120:
            self._accumulate(now, ts, dt, readings, forecast, tz)
        elif dt is not None and dt > 120 and self.acc is not None:
            self.acc = None                                              # a gap: the half-hour isn't trusted
        if self.s.learn_soc_offset:
            self._offsets(ts, dt, obs, readings)
        self.last_ts = ts
        return self._moved(ts)

    def _accumulate(self, now: datetime, ts: float, dt: float, readings, forecast: Forecast | None, tz) -> None:
        bucket = int((ts - dt / 2) // HALF_S) * HALF_S
        if self.acc is not None and self.acc["key"] != bucket:
            self._finalise(self.acc, tz)
            self.acc = None
        if forecast is None:
            return
        if self.acc is None:
            self.acc = {"key": bucket, "cov": 0.0, "sol": 0.0, "load": 0.0, "es": [0.0] * 3, "el": [0.0] * 3,
                        "raw": 0.0, "ok": 0.0, "bias": self.solar_bias()}
        a = self.acc
        a["cov"] += dt
        sol, house = readings.solar_power, readings.house_power
        if sol is not None and house is not None:
            a["ok"] += dt
            a["sol"] += max(0.0, sol) / 1000.0 * dt / 3600.0
            a["load"] += max(0.0, house) / 1000.0 * dt / 3600.0
        bias = a["bias"]
        t0_dt = now - timedelta(seconds=dt)
        hour = str((datetime.fromtimestamp(a["key"], tz) if tz else datetime.fromtimestamp(a["key"])).hour)
        for seg in forecast.segments:
            if seg.end <= t0_dt:
                continue
            if seg.start >= now:
                break
            ov = (min(now, seg.end) - max(t0_dt, seg.start)).total_seconds()
            frac = ov / max((seg.end - seg.start).total_seconds(), 1e-9)
            for i, v in enumerate((seg.solar_kwh.low, seg.solar_kwh.mid, seg.solar_kwh.high)):
                a["es"][i] += v * frac
            for i, v in enumerate((seg.load_kwh.low, seg.load_kwh.mid, seg.load_kwh.high)):
                a["el"][i] += v * frac
            a["raw"] += seg.solar_kwh.mid * frac / max(bias.get(hour, 1.0), 0.01)

    def _decay(self, entry: dict, at: float) -> list[float]:
        c = entry.get("c") or [0.0, 0.0, 0.0]
        last = entry.get("at")
        if last is not None and at > last:
            f = 0.5 ** ((at - last) / (self.s.scenario_half_life_days * 86400.0))
            c = [x * f for x in c]
        return c

    def _count(self, entry: dict, closest: int, at: float) -> dict:
        c = self._decay(entry, at)
        c[closest] += 1.0
        return {"c": c, "at": at}

    @staticmethod
    def _closest(actual: float, figures: list[float]) -> int:
        return min(range(3), key=lambda i: abs(actual - figures[i]))

    def _finalise(self, a: dict, tz) -> None:
        if a["ok"] < MIN_COVER_S or a["cov"] < MIN_COVER_S:
            return
        scale = a["cov"] / a["ok"]
        sol, load = a["sol"] * scale, a["load"] * scale
        start = datetime.fromtimestamp(a["key"], tz or timezone.utc)
        day = start.date().isoformat()
        if day != self.last_day:
            self.days += 1
            self.last_day = day
        at = float(a["key"])
        self._dirty = True
        if a["es"][1] >= DAYLIGHT_KWH:
            if self.s.learn_scenario_weights:
                part = part_of_day(start.hour)
                self.solar[part] = self._count(self.solar.get(part) or {}, self._closest(sol, a["es"]), at)
            if self.s.learn_solar_bias:
                hs = self.by_date.setdefault(day, {})
                t = hs.setdefault(str(start.hour), [0.0, 0.0])
                t[0] += sol
                t[1] += a["raw"] * scale
                for d in sorted(self.by_date)[:-BIAS_DAYS]:
                    del self.by_date[d]
        if self.s.learn_scenario_weights:
            self.load = self._count(self.load, self._closest(load, a["el"]), at)
        key = str(start.hour * 2 + start.minute // 30)
        r = self.resid.setdefault(key, [])
        r.append(round(load - a["el"][1], 4))
        del r[:-SPREAD_SAMPLES]

    # ---- the battery level's offset per mode ----------------------------------------------------
    def _offsets(self, ts: float, dt: float | None, obs: Observation, readings) -> None:
        rep = obs.level_reported
        if rep is None:
            return
        cls = power_class(readings.battery_power)
        if self.pend is not None and cls != self.pend["to"]:
            self.pend = None                                         # it changed again before the window was up
        if self.last_cls is not None and cls != self.last_cls and self.pend is None:
            self.pend = {"from": self.last_cls, "to": cls, "lvl": self.last_level, "t": ts, "e": 0.0}
        p = self.pend
        if p is not None:
            if dt and 0 < dt <= 120 and readings.battery_power is not None:
                p["e"] += abs(readings.battery_power) / 1000.0 * dt / 3600.0
            if ts - p["t"] >= OFFSET_WINDOW_S:
                self._resolve(p, rep)
                self.pend = None
        self.last_cls, self.last_level = cls, rep

    def _resolve(self, p: dict, rep: float) -> None:
        if p["lvl"] is None or p["e"] > OFFSET_ENERGY_KWH or HOLDING not in (p["from"], p["to"]):
            return
        jump = rep - p["lvl"]
        if abs(jump) > OFFSET_MAX:
            return
        mode, target = (p["to"], jump) if p["from"] == HOLDING else (p["from"], -jump)
        new = self.offsets[mode] + OFFSET_GAIN * (target - self.offsets[mode])
        self.offsets[mode] = max(-OFFSET_MAX, min(OFFSET_MAX, new))
        self._dirty = True

    # ---- comfort band bookkeeping (the engine calls this each tick) -----------------------------
    def note_comfort(self, now: datetime, level: float | None, given_up_p: float | None, tz) -> None:
        ts = now.timestamp()
        dt = ts - self.comfort_ts if self.comfort_ts is not None else None
        self.comfort_ts = ts
        day = (now.astimezone(tz) if tz else now).date().isoformat()
        if not self.comfort or self.comfort[-1]["date"] != day:
            self.comfort.append({"date": day, "hours_above": 0.0, "hours_below": 0.0, "given_up": 0.0,
                                 "decisions_changed": 0})
            self.comfort = self.comfort[-14:]
        row = self.comfort[-1]
        if dt and 0 < dt <= 300 and level is not None:
            if level > self.s.comfort_high_soc:
                row["hours_above"] = round(row["hours_above"] + dt / 3600, 4)
            elif level < self.s.comfort_low_soc:
                row["hours_below"] = round(row["hours_below"] + dt / 3600, 4)
        if given_up_p is not None:
            row["given_up"] = round(given_up_p / 100.0, 3)

    def comfort_rows(self, n: int = 7) -> list[dict]:
        return [{"date": r["date"], "hours_above": round(r["hours_above"], 1),
                 "hours_below": round(r["hours_below"], 1), "given_up": r["given_up"],
                 "decisions_changed": r["decisions_changed"]} for r in self.comfort[-n:]]

    # ---- has anything moved enough to revalue ---------------------------------------------------
    def _moved(self, ts: float) -> bool:
        if not self._dirty:
            return False
        if self.last_flag is not None and ts - self.last_flag < MIN_FLAG_S:
            return False                                   # still dirty: looked at again later
        self._dirty = False
        cur, old = self._view(), self.reported
        moved = False
        for p in PARTS:
            prev = old.get("solar", {}).get(p, cur["solar"][p])
            if any(abs(a - b) > MOVED_WEIGHT for a, b in zip(cur["solar"][p], prev, strict=True)):
                moved = True
        if any(abs(a - b) > MOVED_WEIGHT for a, b in zip(cur["load"], old.get("load", cur["load"]), strict=True)):
            moved = True
        ob = old.get("bias", {})
        for h in set(cur["bias"]) | set(ob):
            if abs(cur["bias"].get(h, 1.0) - ob.get(h, 1.0)) > MOVED_BIAS:
                moved = True
        oo = old.get("offsets", {})
        if any(abs(cur["offsets"][m] - oo.get(m, 0.0)) > MOVED_OFFSET for m in MODES):
            moved = True
        if moved:
            self.reported, self.last_flag = cur, ts
        return moved

    def state(self) -> dict:
        return {"solar": self.solar, "load": self.load, "days": self.days, "last_day": self.last_day,
                "by_date": self.by_date, "resid": self.resid, "offsets": dict(self.offsets), "acc": self.acc,
                "pend": self.pend, "last_ts": self.last_ts, "last_cls": self.last_cls,
                "last_level": self.last_level, "comfort": self.comfort, "comfort_ts": self.comfort_ts,
                "reported": self.reported, "last_flag": self.last_flag}
