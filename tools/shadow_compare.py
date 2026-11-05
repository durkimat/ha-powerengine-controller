#!/usr/bin/env python3
"""Compare a shadow run of the PowerEngine integration with the live PowerEngine (docs/plans/native-integration.md).

Both publish the same entities (`sensor.pe_v2_mode`, and the shadow's `sensor.pe_shadow_v2_mode`), so what each said,
minute by minute, is in Home Assistant's history. This reads that history, lines the two up and says where they
disagreed and for how long. Standard library only; run it where it can reach Home Assistant, for example on the Home
Assistant host (the Supervisor's token is in its environment):

    python3 shadow_compare.py --hours 24
    python3 shadow_compare.py --url http://homeassistant.local:8123/api --token-env HA_TOKEN --hours 48

What to expect. The two engines tick a few seconds apart, so a decision changes at slightly different moments: a
disagreement shorter than --min-minutes (default 3) is not reported. Settings and learned data are the live engine's at
the moment the shadow was seeded, so they drift apart slowly: a mismatch that starts late in the run and grows is that,
a mismatch from the start is something to look into. The shadow never sends a command, so the inverter command it
prints is the one it would have sent ("would send" is removed before comparing).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

SHADOW_PREFIX = "pe_shadow_"
STEP_S = 60
WARM_UP = timedelta(minutes=15)           # the shadow's first minutes after it starts are not compared


@dataclass(frozen=True)
class Series:
    """One thing to compare: an entity's state or one of its attributes, with how close two values must be."""
    name: str
    entity: str                    # the live id; the shadow's is the same with pe_shadow_ for pe_
    attribute: str | None = None   # None: the state
    tolerance: float = 0.0         # numbers within this are the same
    clean: str | None = None       # a regex removed from text before comparing


SERIES = (
    Series("decision", "sensor.pe_state_decision"),
    Series("engine v2 mode", "sensor.pe_v2_mode"),
    Series("target SoC (%)", "sensor.pe_v2_mode", "target_soc", tolerance=1.0),
    Series("planned power (W)", "sensor.pe_v2_mode", "power_w", tolerance=300),
    Series("inverter command", "sensor.pe_diag_control", "command", clean=r"\s*\(would send\)"),
    Series("plan headline", "sensor.pe_plan_headline"),
    Series("battery SoC input (%)", "sensor.pe_state_battery_soc", tolerance=1.0),
)
END_OF_RUN = ("sensor.pe_cost_today", "sensor.pe_cost_saved_today", "sensor.pe_state_status",
              "sensor.pe_state_engine", "sensor.pe_state_operation_mode")


def shadow_id(entity_id: str) -> str:
    domain, _, rest = entity_id.partition(".")
    return f"{domain}.{SHADOW_PREFIX}{rest[3:]}" if rest.startswith("pe_") else entity_id


def parse_time(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def steps(rows: list[dict], series: Series) -> list[tuple[datetime, object]]:
    """[(when, value)] in order, from the history rows of one entity (each a state at a time)."""
    out = []
    for row in rows:
        when = row.get("last_changed") or row.get("last_updated")
        if not when:
            continue
        value = row.get("state") if series.attribute is None else (row.get("attributes") or {}).get(series.attribute)
        out.append((parse_time(when), value))
    out.sort(key=lambda item: item[0])
    return out


def value_at(points: list[tuple[datetime, object]], when: datetime) -> object:
    found = None
    for at, value in points:
        if at > when:
            break
        found = value
    return found


def same(a: object, b: object, series: Series) -> bool:
    if series.clean:
        a = re.sub(series.clean, "", a) if isinstance(a, str) else a
        b = re.sub(series.clean, "", b) if isinstance(b, str) else b
    if a in (None, "unknown", "unavailable", "") and b in (None, "unknown", "unavailable", ""):
        return True
    try:
        return abs(float(a) - float(b)) <= series.tolerance            # type: ignore[arg-type]
    except (TypeError, ValueError):
        return a == b


def compare(live: list, shadow: list, series: Series, start: datetime, end: datetime, min_minutes: int = 3) -> dict:
    """How the shadow did against the live engine over [start, end): minutes agreeing, and the disagreements that
    lasted `min_minutes` or more (when, how long, what each said)."""
    live_points, shadow_points = steps(live, series), steps(shadow, series)
    agree = total = 0
    runs: list[dict] = []
    current: dict | None = None
    at = start
    while at < end:
        a, b = value_at(live_points, at), value_at(shadow_points, at)
        total += 1
        if same(a, b, series):
            agree += 1
            if current is not None:
                runs.append(current)
                current = None
        elif current is None:
            current = {"from": at, "minutes": 1, "live": a, "shadow": b}
        else:
            current["minutes"] += 1
        at += timedelta(seconds=STEP_S)
    if current is not None:
        runs.append(current)
    long_runs = [r for r in runs if r["minutes"] >= min_minutes]
    return {"series": series.name, "minutes": total, "agree": agree, "disagreements": long_runs,
            "short": len(runs) - len(long_runs), "longest": max((r["minutes"] for r in runs), default=0)}


def report(results: list[dict], ends: dict, start: datetime, end: datetime, warm_up_to: datetime | None) -> str:
    lines = [f"Shadow run against the live PowerEngine, {start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} UTC"
             + (f" (the shadow's first {int(WARM_UP.total_seconds() // 60)} minutes after "
                f"{warm_up_to:%H:%M} are not compared)" if warm_up_to else ""), ""]
    for r in results:
        pct = 100.0 * r["agree"] / r["minutes"] if r["minutes"] else 0.0
        lines.append(f"{r['series']}: {pct:.1f}% of {r['minutes']} minutes the same"
                     + (f"; {r['short']} short differences ignored" if r["short"] else ""))
        for d in r["disagreements"][:8]:
            lines.append(f"    {d['from']:%d %b %H:%M} for {d['minutes']} min: "
                         f"live {d['live']!r}, shadow {d['shadow']!r}")
        if len(r["disagreements"]) > 8:
            lines.append(f"    ... and {len(r['disagreements']) - 8} more")
    lines += ["", "At the end of the window (live | shadow):"]
    for entity, (live, shadow) in ends.items():
        lines.append(f"    {entity}: {live!r} | {shadow!r}")
    return "\n".join(lines)


def fetch(url: str, token: str, entity_ids: list[str], start: datetime, end: datetime) -> dict[str, list[dict]]:
    """History rows by entity id, straight from Home Assistant's REST API (with attributes)."""
    query = urllib.parse.urlencode({"filter_entity_id": ",".join(entity_ids), "end_time": end.isoformat(),
                                    "significant_changes_only": "0"})
    address = f"{url.rstrip('/')}/history/period/{urllib.parse.quote(start.isoformat())}?{query}"
    request = urllib.request.Request(address, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=120) as response:          # noqa: S310 (the user's own Home Assistant)
        data = json.load(response)
    out: dict[str, list[dict]] = {}
    for rows in data:
        if rows:
            out[rows[0]["entity_id"]] = rows
    return out


def run(history: dict[str, list[dict]], start: datetime, end: datetime, min_minutes: int = 3) -> tuple[str, int]:
    """(the report, how many series disagreed for min_minutes or more)."""
    started = steps(history.get("sensor.pe_shadow_diag_started", []), Series("started", "x"))
    warm_up_to = started[-1][0] if started else None
    compare_from = max(start, warm_up_to + WARM_UP) if warm_up_to else start
    results = [compare(history.get(s.entity, []), history.get(shadow_id(s.entity), []), s, compare_from, end,
                       min_minutes) for s in SERIES]
    ends = {}
    for entity in END_OF_RUN:
        ends[entity] = tuple((history.get(e) or [{}])[-1].get("state") for e in (entity, shadow_id(entity)))
    return report(results, ends, compare_from, end, warm_up_to), sum(1 for r in results if r["disagreements"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://supervisor/core/api", help="Home Assistant's REST API")
    parser.add_argument("--token-env", default="SUPERVISOR_TOKEN", help="the environment variable with the token")
    parser.add_argument("--hours", type=float, default=24.0, help="how far back to look")
    parser.add_argument("--min-minutes", type=int, default=3, help="shorter disagreements are not reported")
    options = parser.parse_args(argv)
    token = os.environ.get(options.token_env, "")
    if not token:
        print(f"{options.token_env} is not set", file=sys.stderr)
        return 2
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=options.hours)
    ids = sorted({s.entity for s in SERIES} | {shadow_id(s.entity) for s in SERIES} | set(END_OF_RUN)
                 | {shadow_id(e) for e in END_OF_RUN} | {"sensor.pe_shadow_diag_started"})
    history = fetch(options.url, token, ids, start, end)
    text, differing = run(history, start, end, options.min_minutes)
    print(text)
    return 1 if differing else 0


if __name__ == "__main__":
    sys.exit(main())
