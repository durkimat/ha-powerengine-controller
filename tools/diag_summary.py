#!/usr/bin/env python3
"""A one-screen summary of a PowerEngine diagnostics export (the Health tab's "Export diagnostics" JSON).

    tools/diag_summary.py export.json [--since 2026-09-29T18:00] [--json]

For a routine check: read this, then open the full JSON only for what it flags. Standard library only. Every section
copes with being absent (older exports), so a missing part is left out, not an error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

FLIP_FLOP_S = 120  # the same pair alternating within this many seconds is a flip-flop
ATTR_PEAK_BYTES = 12 * 1024  # attribute sizes are shown only above this (HA's limit is 16 KB)
PLAN_SLOTS = 12
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# Only the lines the app itself writes for these (see powerengine.py): "Decision: <sentence>", "RAM remote control: ..."
RAM_SENT = re.compile(r"^RAM remote control(?::\s*(?:Off|Hold|Force (?:charge|discharge) at)|\s+Off\b)")
STARTING = re.compile(r"starting\b", re.I)
AXLE = re.compile(r"axle", re.I)
NUMBER = re.compile(r"\d+(?:\.\d+)?")


# --- small helpers ------------------------------------------------------------------------------------------


def as_dict(x):
    return x if isinstance(x, dict) else {}


def as_list(x):
    return x if isinstance(x, list) else []


def parse_time(s):
    """ISO string -> aware datetime (naive means UTC); None if it isn't one."""
    if not isinstance(s, str) or not s:
        return None
    try:
        t = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def zone(name):
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name) if name else timezone.utc
    except Exception:
        return timezone.utc


def hhmm(t, tz, seconds=False):
    return t.astimezone(tz).strftime("%H:%M:%S" if seconds else "%H:%M") if t else "?"


def stamp(t, tz):
    return t.astimezone(tz).strftime("%d %b %H:%M") if t else "?"


def version_tuple(v):
    m = re.fullmatch(r"\s*v?(\d+(?:\.\d+)*)\s*", str(v or ""))
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def older(a, b):
    x, y = version_tuple(a), version_tuple(b)
    if x is None or y is None:
        return None
    n = max(len(x), len(y))
    return x + (0,) * (n - len(x)) < y + (0,) * (n - len(y))


def action_of(text):
    """ "Would grid-charge to 80%: cheap rate" -> "grid-charge" (the action without its level, power or caveat)."""
    head = str(text).split(":", 1)[0].strip().lower()
    head = re.sub(r"^would\s+", "", head)
    head = re.sub(r"\s+to\s+\d+%.*$|\s+at\s+[\d.]+\s*kw.*$|\s*\(.*$", "", head)
    return head or "?"


# --- sections -----------------------------------------------------------------------------------------------


def log_lines(app, since):
    out = []
    for x in as_list(app.get("log")):
        if not isinstance(x, dict):
            continue
        t = parse_time(x.get("t"))
        if since is not None and (t is None or t < since):
            continue
        out.append({"t": t, "level": str(x.get("level") or "INFO").upper(), "msg": str(x.get("msg") or "")})
    return out


def warnings_of(lines, tz):
    groups: dict[str, dict] = {}
    for x in lines:
        if x["level"] not in ("WARNING", "ERROR", "CRITICAL"):
            continue
        key = (x["level"], NUMBER.sub("N", x["msg"]))
        g = groups.setdefault(
            key, {"level": x["level"], "msg": x["msg"][:200], "count": 0, "first": x["t"], "last": x["t"]}
        )
        g["count"] += 1
        if x["t"] and (g["first"] is None or x["t"] < g["first"]):
            g["first"] = x["t"]
        if x["t"] and (g["last"] is None or x["t"] > g["last"]):
            g["last"] = x["t"]
    rows = sorted(groups.values(), key=lambda g: (-g["count"], g["msg"]))
    return [
        {
            "level": g["level"],
            "count": g["count"],
            "msg": g["msg"],
            "first": g["first"].isoformat(timespec="seconds") if g["first"] else None,
            "last": g["last"].isoformat(timespec="seconds") if g["last"] else None,
        }
        for g in rows
    ]


def per_hour(times, tz):
    c = Counter(t.astimezone(tz).strftime("%d %H:00") for t in times if t)
    return dict(sorted(c.items()))


def decisions_of(lines, tz):
    """Decisions are logged when the action, rule or target changes: counts per hour, flips and flip-flops."""
    ds = [
        (x["t"], action_of(x["msg"][len("Decision:") :])) for x in lines if x["msg"].startswith("Decision:") and x["t"]
    ]
    ds.sort(key=lambda d: d[0])
    flips = sum(1 for a, b in zip(ds, ds[1:], strict=False) if a[1] != b[1])
    ff: dict[tuple, dict] = {}
    for i in range(2, len(ds)):
        (t0, a0), (_, a1), (t2, a2) = ds[i - 2], ds[i - 1], ds[i]
        if a0 == a2 and a0 != a1 and (t2 - t0).total_seconds() <= FLIP_FLOP_S:
            pair = tuple(sorted((a0, a1)))
            g = ff.setdefault(pair, {"pair": " <-> ".join(pair), "count": 0, "first": t0})
            g["count"] += 1
    return {
        "total": len(ds),
        "per_hour": per_hour([d[0] for d in ds], tz),
        "action_flips": flips,
        "flip_flops": [
            {"pair": g["pair"], "count": g["count"], "first": g["first"].isoformat(timespec="seconds")}
            for g in ff.values()
        ],
    }


def ram_of(lines, journal, since, tz):
    sent = [x["t"] for x in lines if x["t"] and RAM_SENT.match(x["msg"])]
    source = "log"
    if not sent:
        # the log ring is short; the write journal also records each RAM setting (3 days)
        source = "journal"
        for e in as_list(journal):
            t = parse_time(as_dict(e).get("t"))
            if (
                t
                and "battery_control_override" in str(as_dict(e).get("entity", "")).lower()
                and (since is None or t >= since)
            ):
                sent.append(t)
    return {"total": len(sent), "source": source if sent else None, "per_hour": per_hour(sent, tz)}


def smart_requests_of(app, since, tz):
    rows = []
    for a in as_list(app.get("smart_requests")):
        a = as_dict(a)
        t = parse_time(a.get("time"))
        if since is not None and (t is None or t < since):
            continue
        rows.append(
            {
                "time": a.get("time"),
                "from": a.get("from"),
                "to": a.get("to"),
                "why": a.get("why") or "",
                "result": a.get("result") or "pending",
                "by": a.get("by"),
            }
        )
    return rows


def plan_of(app, generated, tz):
    plan = as_dict(app.get("plan"))
    slots = []
    for s in as_list(plan.get("slots")):
        s = as_dict(s)
        t = parse_time(s.get("start"))
        if t is None or (generated is not None and t + timedelta(minutes=30) <= generated):
            continue
        slots.append((t, s))
    slots.sort(key=lambda p: p[0])
    return (
        {
            "made_at": plan.get("made_at"),
            "next": [
                {
                    "time": t.isoformat(timespec="minutes"),
                    "action": s.get("action"),
                    "soc": s.get("soc"),
                    "price_p": s.get("price_p"),
                }
                for t, s in slots[:PLAN_SLOTS]
            ],
        }
        if slots
        else None
    )


def writes_of(app):
    w = as_dict(app.get("writes"))
    if not w:
        return None
    obs = as_dict(w.get("observed"))
    return {
        "budget": w.get("budget"),
        "since": w.get("since"),
        "observed_today": obs.get("today"),
        "observed_per_day": obs.get("per_day"),
        "observed_days": obs.get("days"),
        "observed_total": obs.get("total"),
        "years_at_this_rate": obs.get("years"),
        "ram_today": w.get("ram"),
        "staged_today": w.get("staged"),
    }


def early_of(app):
    """#175: charge targets reached early in a half-hour, and what the replan did (or why the hold stayed)."""
    e = as_dict(app.get("early_target"))
    if not e or not e.get("records"):
        return None
    return {"count": e.get("count") or len(as_list(e.get("records"))), "since": e.get("since"),
            "by_outcome": as_dict(e.get("by_outcome")), "replan_chose": as_dict(e.get("replan_chose"))}


def summarise(export, since=None):
    export = as_dict(export)
    app = as_dict(export.get("app"))
    meta = as_dict(app.get("app"))
    tz = zone(meta.get("timezone"))
    generated = parse_time(export.get("generated")) or parse_time(meta.get("generated"))
    lines = log_lines(app, since)
    levels = Counter(x["level"] for x in lines)
    mode = as_dict(app.get("mode"))
    cfg = as_dict(app.get("config"))
    card, min_card = export.get("card_version"), meta.get("min_card_version")
    sizes = [as_dict(x) for x in as_list(app.get("attribute_sizes"))]
    smart_slots = as_dict(app.get("smart_slots"))
    out = {
        "generated": export.get("generated") or meta.get("generated"),
        "app_version": meta.get("version"),
        "card_version": card,
        "min_card_version": min_card,
        "card_older_than_minimum": older(card, min_card) if min_card else None,
        "timezone": meta.get("timezone"),
        "mode": {k: mode.get(k) for k in ("configured", "effective", "reason", "halted")} if mode else None,
        "site": cfg.get("site"),
        "since": since.isoformat(timespec="seconds") if since else None,
        "log": {
            "lines": len(lines),
            "levels": dict(sorted(levels.items(), key=lambda kv: LEVELS.index(kv[0]) if kv[0] in LEVELS else 99)),
        }
        if lines
        else None,
        "warnings": warnings_of(lines, tz),
        "decisions": decisions_of(lines, tz) if lines else None,
        "ram": ram_of(lines, app.get("journal"), since, tz) if (lines or app.get("journal")) else None,
        "smart_requests": smart_requests_of(app, since, tz),
        "smart_slots": {
            k: smart_slots.get(k) for k in ("slots", "used", "cancelled", "cut_short", "upcoming", "car_kwh")
        }
        | {"recent": as_list(smart_slots.get("recent"))}
        if smart_slots
        else None,
        "axle": [
            {
                "time": x["t"].isoformat(timespec="seconds") if x["t"] else None,
                "level": x["level"],
                "msg": x["msg"][:200],
            }
            for x in lines
            if AXLE.search(x["msg"])
        ][-10:],
        "restarts": [
            {"time": x["t"].isoformat(timespec="seconds") if x["t"] else None, "msg": x["msg"][:100]}
            for x in lines
            if x["msg"].startswith("PowerEngine") and STARTING.search(x["msg"])
        ],
        "big_attributes": [
            {"sensor": x.get("sensor"), "peak_bytes": x.get("peak_bytes"), "last_bytes": x.get("last_bytes")}
            for x in sizes
            if isinstance(x.get("peak_bytes"), (int, float)) and x["peak_bytes"] > ATTR_PEAK_BYTES
        ],
        "plan": plan_of(app, generated, tz),
        "writes": writes_of(app),
        "early_target": early_of(app),
    }
    return out


# --- text ---------------------------------------------------------------------------------------------------


def render(s):
    tz = zone(s.get("timezone"))
    T = lambda iso: stamp(parse_time(iso), tz)  # noqa: E731
    o = []
    add = o.append
    add(
        "PowerEngine diagnostics: generated "
        + (T(s["generated"]) if s.get("generated") else "?")
        + f" ({s.get('timezone') or 'tz ?'})"
    )
    card = f"card {s.get('card_version') or '?'}"
    if s.get("card_older_than_minimum"):
        card += f"  ** OLDER than the app's minimum {s.get('min_card_version')} **"
    add(f"  app {s.get('app_version') or '?'}, {card}" + (f", window since {T(s['since'])}" if s.get("since") else ""))
    m = s.get("mode")
    if m:
        add(
            f"  mode: {m.get('effective')} (configured {m.get('configured')})"
            + (" HALTED" if m.get("halted") else "")
            + (f" - {m.get('reason')}" if m.get("reason") else "")
        )
    site = s.get("site")
    if isinstance(site, dict) and site:
        add("  site: " + ", ".join(f"{k}={v}" for k, v in site.items()))
    lg = s.get("log")
    if lg:
        add(f"log: {lg['lines']} lines: " + ", ".join(f"{k} {v}" for k, v in lg["levels"].items()))
    elif "log" in s:
        add("log: none in this export/window")
    ws = s.get("warnings") or []
    if ws:
        add(f"warnings/errors ({len(ws)} distinct):")
        for w in ws[:15]:
            rng = f"{T(w['first'])}" if w["count"] == 1 else f"{T(w['first'])} .. {T(w['last'])}"
            add(f"  {w['level'][0]} x{w['count']} {rng}: {w['msg'][:110]}")
        if len(ws) > 15:
            add(f"  ... and {len(ws) - 15} more (see the JSON)")
    d = s.get("decisions")
    if d is not None:
        ph = d["per_hour"]
        add(
            f"decisions: {d['total']} logged, {d['action_flips']} action flips"
            + (f"; busiest hour {max(ph, key=ph.get)} ({max(ph.values())})" if ph else "")
        )
        if ph:
            add("  per hour: " + " ".join(f"{k.split(' ')[1][:2]}h={v}" for k, v in ph.items()))
        for f in d["flip_flops"]:
            add(f"  FLIP-FLOP {f['pair']} x{f['count']} (within {FLIP_FLOP_S} s), first {T(f['first'])}")
        if not d["flip_flops"] and d["total"]:
            add("  no flip-flops")
    r = s.get("ram")
    if r is not None:
        ph = r["per_hour"]
        add(
            f"RAM commands: {r['total']} total"
            + (f" (from the {r['source']})" if r["source"] else "")
            + ("; per hour " + " ".join(f"{k.split(' ')[1][:2]}h={v}" for k, v in ph.items()) if ph else "")
        )
    sr = s.get("smart_requests") or []
    if sr:
        add(f"smart-slot requests ({len(sr)}):")
        for a in sr[-10:]:
            add(
                f"  {T(a['time'])} {a['from'] or '-'} -> {a['to'] or '-'}"
                f"  why: {str(a['why'])[:50] or '-'}  result: {a['result']}"
            )
    ss = s.get("smart_slots")
    if ss:
        add(
            f"smart slots (recent): {ss.get('slots')} finished, {ss.get('used')} used, "
            f"{ss.get('cancelled')} cancelled, "
            f"{ss.get('cut_short')} cut short, {ss.get('upcoming')} upcoming, car {ss.get('car_kwh')} kWh"
        )
        for x in as_list(ss.get("recent"))[-6:]:
            x = as_dict(x)
            add(
                f"  {x.get('day')} {x.get('time')} {x.get('status')} "
                f"plan {x.get('planned_kwh')} car {x.get('car_kwh')} kWh"
            )
    ax = s.get("axle") or []
    if ax:
        add(f"Axle events in the log ({len(ax)}):")
        for x in ax[-5:]:
            add(f"  {T(x['time'])} {x['msg'][:110]}")
    rs = s.get("restarts") or []
    add(f"restarts: {len(rs)}" + ("  at " + ", ".join(T(x["time"]) for x in rs[-6:]) if rs else ""))
    big = s.get("big_attributes") or []
    if big:
        add(
            "attributes above 12 KB (limit 16 KB): " + "; ".join(f"{x['sensor']} peak {x['peak_bytes']} B" for x in big)
        )
    p = s.get("plan")
    if p:
        add(f"plan: next {len(p['next'])} slots (made {T(p['made_at']) if p.get('made_at') else '?'}):")
        add(
            "  "
            + "  ".join(
                f"{hhmm(parse_time(x['time']), tz)} {x['action']} {x['soc']}% {x['price_p']}p" for x in p["next"][:6]
            )
        )
        if len(p["next"]) > 6:
            add(
                "  "
                + "  ".join(
                    f"{hhmm(parse_time(x['time']), tz)} {x['action']} {x['soc']}% {x['price_p']}p"
                    for x in p["next"][6:]
                )
            )
    e = s.get("early_target")
    if e:
        parts = [f"{k} x{v.get('count')} ({v.get('minutes_left')} min left)" for k, v in e["by_outcome"].items()]
        add(f"early targets (#175), {e['count']} since {e['since']}: " + "; ".join(parts))
        if e["replan_chose"]:
            add("  replans chose: " + ", ".join(f"{k} x{v}" for k, v in e["replan_chose"].items()))
    w = s.get("writes")
    if w:
        add(
            f"write budget: observed {w['observed_today']} today, "
            f"{w['observed_per_day']}/day over {w['observed_days']} days, "
            f"total {w['observed_total']}"
            + (f", {w['years_at_this_rate']} years at this rate" if w.get("years_at_this_rate") else "")
            + f" (budget {w['budget']}; RAM today {w['ram_today']}, staged {w['staged_today']})"
        )
    return "\n".join(o)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Summarise a PowerEngine diagnostics export (JSON).")
    ap.add_argument("file", help="the export, or - for stdin")
    ap.add_argument(
        "--since", help="only log lines, requests and RAM writes at or after this ISO time (UTC if no zone)"
    )
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args(argv)
    since = None
    if a.since:
        since = parse_time(a.since)
        if since is None:
            ap.error(f"--since: not an ISO time: {a.since!r}")
    try:
        with sys.stdin if a.file == "-" else open(a.file, encoding="utf-8") as fh:
            export = json.load(fh)
    except (OSError, ValueError) as err:
        print(f"diag_summary: cannot read {a.file}: {err}", file=sys.stderr)
        return 2
    s = summarise(export, since)
    print(json.dumps(s, indent=1, default=str) if a.json else render(s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
