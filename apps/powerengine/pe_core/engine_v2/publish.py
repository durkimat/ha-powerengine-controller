"""The published sensors of engine v2: states and attributes (the card's contract, docs/plans/engine-v2-build.md).

`entity_states` returns `{publish key: (state, attributes)}` (the entity is `sensor.pe_<key>`); every attribute set is
kept under 15,000 bytes (HA's recorder skips more than 16 KB). Times are ISO 8601 with an offset, prices and values in
pence per kWh to 2 places, levels to 1.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta

from .settings import SETTINGS, V2Settings, catalogue
from .types import MODE_LABEL, ModeState, StepOutput, ValueResult

LIMIT = 14_500                       # below the app's 15,000 byte warning
CURVE_PCTS = tuple(range(0, 101, 10))
CURVE_MAX_ROWS = 48
REASON_MAX = 220


def _iso(t: datetime | None, tz=None) -> str | None:
    if t is None:
        return None
    return (t.astimezone(tz) if tz else t).isoformat(timespec="seconds")


def _r(x, n: int = 2):
    return None if x is None else round(float(x), n)


def size(attrs: dict) -> int:
    return len(json.dumps(attrs, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def _fit(attrs: dict, reducers) -> dict:
    """Apply the reducers one after another until the attributes fit under LIMIT."""
    for fn in reducers:
        if size(attrs) <= LIMIT:
            break
        fn(attrs)
    return attrs


# ---- the mode ----------------------------------------------------------------------------------
def _mode(out: StepOutput, engine, tz) -> tuple[str, dict]:
    m: ModeState = out.mode
    exits = [{"kind": e.kind, "text": e.text, "expected_at": _iso(e.expected_at, tz), "first": False}
             for e in m.exits]
    timed = [(e.expected_at, i) for i, e in enumerate(m.exits) if e.expected_at is not None]
    if timed:
        exits[min(timed)[1]]["first"] = True
    elif exits:
        exits[0]["first"] = True
    vr = engine.vr
    obs = out.observation
    attrs = {"label": MODE_LABEL.get(m.mode, m.mode), "since": _iso(m.since, tz), "why": m.why, "rule": m.rule,
             "chosen_by": m.chosen_by, "target_soc": _r(m.target_soc, 1), "power_w": None if m.power_w is None
             else int(round(m.power_w)), "exits": exits, "deadline": _iso(m.deadline, tz),
             "level_reported": _r(obs.level_reported, 1), "level_filtered": _r(obs.level_filtered, 1),
             "sending": bool(engine.sending), "not_sending_reason": engine.not_sending_reason,
             "values_at": _iso(vr.made_at, tz) if vr else None, "values_because": vr.because if vr else None}
    return m.mode, attrs


# ---- the value now -----------------------------------------------------------------------------
def scale_max(lines) -> int:
    """The top of the value bar: 40p, or more when the real prices need it (grid-event values are off the scale)."""
    top = max(lines.buy_line_p, lines.sell_line_p, lines.use_line_p, lines.store_sun_line_p, lines.import_p,
              lines.export_p, 30.0)
    return max(40, int(math.ceil(top * 1.25 / 10.0) * 10))


def _value(out: StepOutput) -> tuple[str, dict]:
    ln = out.mode.lines
    if ln is None:
        return "unknown", {}
    attrs = {"value_p": _r(ln.value_p), "buy_line_p": _r(ln.buy_line_p), "sell_line_p": _r(ln.sell_line_p),
             "use_line_p": _r(ln.use_line_p), "store_sun_line_p": _r(ln.store_sun_line_p),
             "import_p": _r(ln.import_p), "export_p": _r(ln.export_p), "charge_target_soc": _r(ln.charge_target_soc, 1),
             "sell_floor_soc": _r(ln.sell_floor_soc, 1), "level": _r(out.observation.level_filtered, 1),
             "scale_max_p": scale_max(ln)}
    return f"{ln.value_p:.2f}", attrs


# ---- the timeline ------------------------------------------------------------------------------
def _prices(vr: ValueResult, tz) -> list[dict]:
    rows: list[dict] = []
    for seg in vr.forecast.segments:
        row = {"start": seg.start, "end": seg.end, "import_p": _r(seg.import_p), "export_p": _r(seg.export_p),
               "slot_prob": _r(seg.slot_prob), "slot_import_p": _r(seg.slot_import_p) if seg.slot_prob is not None
               else None, "event": bool(seg.event), "free": bool(seg.free), "estimated": bool(seg.price_estimated)}
        last = rows[-1] if rows else None
        if last and all(last[k] == row[k] for k in ("import_p", "export_p", "slot_prob", "slot_import_p", "event",
                                                    "free", "estimated")) and last["end"] == row["start"]:
            last["end"] = row["end"]
        else:
            rows.append(row)
    out = []
    for r in rows:
        d = {"start": _iso(r["start"], tz), "end": _iso(r["end"], tz), "import_p": r["import_p"],
             "export_p": r["export_p"], "slot_prob": r["slot_prob"], "event": r["event"], "free": r["free"],
             "estimated": r["estimated"]}
        if r["slot_prob"] is not None:
            d["slot_import_p"] = r["slot_import_p"]
        out.append(d)
    return out


def _thin_path(a: dict) -> None:
    p = a["path"]
    for k in ("mid", "low", "high"):
        p[k] = p[k][::2]
    p["step_min"] = p["step_min"] * 2


def _short_reasons(n: int):
    def f(a: dict) -> None:
        for it in a["items"]:
            it["reason"] = it["reason"][:n]
    return f


def _cap_items(n: int):
    def f(a: dict) -> None:
        a["items"] = a["items"][:n]
    return f


def _timeline(vr: ValueResult | None, settings: V2Settings, hard_floor: float, tz) -> tuple[str, dict]:
    if vr is None:
        return "unknown", {}
    p = vr.path or {}
    attrs = {"now": _iso(vr.made_at, tz), "because": vr.because, "floor_soc": _r(hard_floor, 1),
             "reserve_soc": _r(settings.reserve_soc, 1),
             "items": [{"mode": it.mode, "start": _iso(it.start, tz), "end": _iso(it.end, tz),
                        "level_start": _r(it.level_start, 1), "level_end": _r(it.level_end, 1), "until": it.until,
                        "reason": it.reason[:REASON_MAX]} for it in vr.timeline],
             "path": {"start": p.get("start"), "step_min": p.get("step_min", 15),
                      **{k: [_r(x, 1) for x in p.get(k, [])] for k in ("mid", "low", "high")}},
             "prices": _prices(vr, tz),
             "cost_expected": _r(vr.cost_expected_p / 100), "cost_selfuse": _r(vr.cost_selfuse_p / 100),
             "comfort_given_up": _r(vr.comfort_given_up_p / 100) if vr.comfort_given_up_p is not None else None,
             "calc_s": _r(vr.calc_s)}
    _fit(attrs, [_short_reasons(120), _thin_path, _short_reasons(60), _short_reasons(0), _cap_items(40),
                 _cap_items(24)])
    return _iso(vr.made_at, tz), attrs


# ---- the value curve ---------------------------------------------------------------------------
def _curve(vr: ValueResult | None, tz) -> tuple[str, dict]:
    if vr is None or not vr.lam or not vr.forecast.segments:
        return "unknown", {}
    segs = vr.forecast.segments
    start = vr.made_at.replace(minute=0, second=0, microsecond=0)
    values = []
    for i in range(CURVE_MAX_ROWS):
        t = max(start + timedelta(hours=i), segs[0].start)
        if t >= segs[-1].end:
            break
        k = next(j for j, g in enumerate(segs) if g.start <= t < g.end)
        row = vr.lam[k]
        n = len(row)
        values.append([_r(row[min(n - 1, int(round(p / 100 * (n - 1))))], 2) for p in CURVE_PCTS])
    attrs = {"start": _iso(start, tz), "step_min": 60, "levels": list(CURVE_PCTS), "values": values,
             "unit": "p/kWh"}
    return _iso(vr.made_at, tz), attrs


# ---- triggers and diagnostics ------------------------------------------------------------------
def _triggers(out: StepOutput, engine) -> tuple[str, dict]:
    now = out.observation.now
    today = engine.triggers.health(now)
    today.pop("day", None)
    today.pop("pending", None)
    attrs = {"recent": list(reversed(engine.triggers.recent))[:30], "today": today}
    return str(today["revalues"]), attrs


def _diag(out: StepOutput, engine) -> tuple[str, dict]:
    attrs = engine.learner.diag()
    attrs["filter_gap_max_today"] = round(engine.observer.gap_max_today, 2)
    attrs["comfort"] = engine.learner.comfort_rows()
    return ("learning" if engine.learner.days < 3 else "ok"), attrs


def entity_states(out: StepOutput, engine, settings: V2Settings, tz) -> dict[str, tuple[str, dict]]:
    """Every engine v2 sensor for this tick: {publish key: (state, attributes)}."""
    floor = engine.last_facts.hard_floor_soc if engine.last_facts is not None else 12.0
    res = {"state_engine": ("v2", {"v2_available": True}),
           "v2_mode": _mode(out, engine, tz), "v2_value": _value(out),
           "v2_timeline": _timeline(out.value, settings, floor, tz), "v2_value_curve": _curve(out.value, tz),
           "v2_triggers": _triggers(out, engine), "diag_v2": _diag(out, engine)}
    return res


def settings_state(settings: V2Settings) -> tuple[str, dict]:
    """sensor.pe_diag_v2_settings: the catalogue with the values in use (the config page's schema)."""
    return str(len(SETTINGS)), catalogue(settings)

