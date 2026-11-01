"""Why a day's engine v2 saving fell short of the best possible: where the gap opened, in plain words.

The nightly replay costs every half-hour of v2's run and of the perfect-foresight bound; this sums both by part of
the day and says which part cost most. Pure: no files, no app. The result of `build` is kept in the day's result file
(`why`) and shown when a day row is opened on the Costs page."""

from __future__ import annotations

from datetime import datetime

# (key, label, first hour, last hour + 1) for half-hours that are not in a grid event
PARTS = (("night", "Overnight (00:00 to 07:00)", 0, 7), ("day", "Daytime (07:00 to 16:00)", 7, 16),
         ("evening", "Evening (16:00 to 24:00)", 16, 24))
EVENT = ("event", "Grid event")
SMALL_GBP = 0.05                      # a part that differs by less than this is not worth a mention


def _part(start: datetime, is_event: bool) -> tuple[str, str]:
    if is_event:
        return EVENT
    hour = start.hour
    for key, label, a, b in PARTS:
        if a <= hour < b:
            return key, label
    return PARTS[-1][0], PARTS[-1][1]


def block_totals(halves: list[tuple[str, float, bool]], tz) -> dict[str, float]:
    """{part key: cost GBP} from [(half-hour start iso, cost GBP, in a grid event)]."""
    out: dict[str, float] = {}
    for start, cost, event in halves:
        key, _ = _part(datetime.fromisoformat(start).astimezone(tz), event)
        out[key] = out.get(key, 0.0) + cost
    return out


def build(v2: dict, bound: dict, tz, real_event_kwh: float | None = None, what_if: dict | None = None) -> dict | None:
    """The `why` of a day from the v2 run and the bound (their summaries, with `halves`), or None without halves.

    gap is v2's adjusted cost less the bound's: positive means v2 did worse. It is the sum of the parts' cash gaps and
    the value of the battery level each ended the day on ("end")."""
    if not v2.get("halves") or not bound.get("halves"):
        return None
    a, b = block_totals(v2["halves"], tz), block_totals(bound["halves"], tz)
    parts = []
    for key, label in (EVENT, *((k, lb) for k, lb, *_ in PARTS)):
        if key in a or key in b:
            va, vb = a.get(key, 0.0), b.get(key, 0.0)
            parts.append({"key": key, "label": label, "v2": round(va, 2), "bound": round(vb, 2),
                          "gap": round(va - vb, 2)})
    end_gap = -(v2.get("endval_gbp", 0.0) - bound.get("endval_gbp", 0.0))
    out = {"gap": round(v2["adj_cost_gbp"] - bound["adj_cost_gbp"], 2), "parts": parts, "end": round(end_gap, 2),
           "end_soc": {"v2": _r1(v2.get("end_soc")), "bound": _r1(bound.get("end_soc"))},
           "event_kwh": {"real": _r1(real_event_kwh), "v2": _r1(v2.get("event_kwh")),
                         "bound": _r1(bound.get("event_kwh"))},
           "peak_kwh": {"v2": _r1(v2.get("peak_charge_kwh")), "bound": _r1(bound.get("peak_charge_kwh"))},
           "changes": {"v2": v2.get("mode_changes"), "bound": bound.get("mode_changes")}}
    if what_if:
        out["what_if"] = {"real_sun": {"saving": what_if.get("saving"), "delta": what_if.get("delta")}}
    out["lines"] = lines(out)
    return out


def _r1(x):
    return None if x is None else round(float(x), 1)


def lines(why: dict) -> list[str]:
    """A few plain sentences from a `why`: the biggest part of the gap first, then what differed."""
    gap = why["gap"]
    if gap < SMALL_GBP:
        return ["Engine v2 was within 5p of the best possible this day."]
    items = [(p["gap"], p["label"], p["key"]) for p in why["parts"]]
    items.append((why["end"], "the battery level left at the end", "end"))
    items = sorted((i for i in items if i[0] >= SMALL_GBP), reverse=True)
    out = []
    if items:
        worst = "; ".join(f"{label[0].lower() + label[1:]} £{g:.2f}" for g, label, _ in items[:3])
        out.append(f"Engine v2 saved £{gap:.2f} less than the best possible. Where: {worst}.")
    ev = why["event_kwh"]
    if any(k == "event" for _, _, k in items) and ev.get("v2") is not None and ev.get("bound") is not None:
        real = f" (the real system sent {ev['real']} kWh)" if ev.get("real") is not None else ""
        out.append(f"In the grid event v2 exported {ev['v2']} kWh, the best possible {ev['bound']} kWh{real}.")
    es = why["end_soc"]
    if any(k == "end" for _, _, k in items) and es.get("v2") is not None and es.get("bound") is not None:
        out.append(f"v2 ended the day on {es['v2']}% charge, the best possible on {es['bound']}%.")
    sun = (why.get("what_if") or {}).get("real_sun")
    if sun and sun.get("delta") is not None:
        d = sun["delta"]
        lead = "Given the real sun and house use as the forecast, v2 would have"
        if abs(d) < SMALL_GBP:
            out.append(f"{lead} done about the same: the forecast was not the problem.")
        else:
            more, what = ("more", "cost") if d > 0 else ("less", "happened to save")
            out.append(f"{lead} saved £{abs(d):.2f} {more}: that is what the forecast error {what}.")
    pk = why["peak_kwh"]
    if pk.get("v2") is not None and pk.get("bound") is not None and pk["v2"] - pk["bound"] >= 0.5:
        out.append(f"v2 charged {pk['v2']} kWh from the grid at the peak rate, the best possible {pk['bound']} kWh.")
    return out
