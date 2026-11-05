"""What-if replays that say why engine v2 fell short of the best possible, beyond where the cost fell (why.py).

One variant for now: engine v2 given the day's real sun and its real house use as the forecast, with everything else
as it was (prices, smart slots, grid events as announced). The difference from the ordinary v2 run is what the forecast
error cost; what is left of the gap to the best possible is the engine's choices and the events.

Runs in the nightly runner after the ordinary comparison, in the same process and with the same machinery."""

from __future__ import annotations

import copy
import dataclasses
from datetime import date

from ..forecast import LoadProfile
from .replay import Scenario, run_app
from .snapfeed import FED_ROLES, SnapshotFeed

SUN_ROLES = ("solar_forecast_today", "solar_forecast_tomorrow", "solar_forecast_day3")


def actual_profile(house_kwh: list[float], day: date, profile_days: float = 30.0) -> LoadProfile:
    """A house profile that is the day's real house use: W for each half-hour of the day, under both day types."""
    watts = {}
    for hh, kwh in enumerate(house_kwh[:48]):
        for weekend in (False, True):
            watts[(weekend, hh)] = max(0.0, float(kwh)) * 2000.0
    return LoadProfile(watts=watts, days=profile_days)


def real_sun_scenario(sc: Scenario, snapshot, house_kwh: list[float], day: date) -> Scenario:
    """The scenario with the sun as it happened (the pack's own forecast made equal to the actual solar, served by the
    world instead of the snapshot's Solcast figures) and the house profile as the day's real use."""
    pack = copy.deepcopy(sc.pack)
    d = pack["days"][sc.day]
    d["forecast"] = list(d["solar"])
    roles = tuple(r for r in FED_ROLES if r not in SUN_ROLES)
    profile = actual_profile(house_kwh, day)
    return dataclasses.replace(sc, pack=pack, feed=SnapshotFeed(snapshot, roles), profile_at=lambda _when: profile)


def saving_of(run: dict, outcome: dict, sc: Scenario) -> float:
    """A run's saving against plain self-use, end level adjusted, as `replay.compare_day` works it out."""
    su = outcome["runs"]["selfuse"]
    cheapest = outcome["cheapest_rate"]
    endval = (run["end_soc"] - su["end_soc"]) / 100 * sc.capacity_kwh * cheapest
    return su["adj_cost_gbp"] - (run["cost_gbp"] - endval)


def run_real_sun(sc: Scenario, day_input, outcome: dict, say=lambda *a: None) -> dict:
    """v2 with the real sun and house load: {"saving", "delta" (against the ordinary v2 run), "end_soc"}."""
    say(f"{sc.day}: engine v2 with the real sun and house load ...")
    house = [float(x) for x in day_input.pack["days"][sc.day]["house"]]
    variant = real_sun_scenario(sc, day_input.snapshot, house, day_input.day)
    run = run_app(variant, "v2")
    saving = saving_of(run, outcome, sc)
    ordinary = outcome["runs"]["v2"]["adj_save_gbp"]
    return {"saving": round(saving, 3), "delta": round(saving - ordinary, 3), "end_soc": round(run["end_soc"], 1),
            "wall_s": run.get("wall_s")}
