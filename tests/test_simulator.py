from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from pe_core import kraken
from pe_core.planner import Params
from pe_core.simjob import SimStore, run
from pe_core.simulator import _shift_car, actual_cost, opportunities, scenarios, summarise

LON = ZoneInfo("Europe/London")
UTC = timezone.utc
DAYS = ["2026-09-20", "2026-09-21", "2026-09-22"]


def day_records(d, car_at=36):
    start = datetime.combine(date.fromisoformat(d), datetime.min.time(), tzinfo=LON).astimezone(UTC)
    out = []
    for i in range(48):
        t = start + timedelta(minutes=30 * i)
        cheap = i < 10
        out.append(
            {
                "start": t.isoformat(),
                "seconds": 1800,
                "house": 0.4,
                "car": 3.0 if i in (car_at, car_at + 1) else 0,
                "solar": 0.8 if 20 <= i < 30 else 0.0,
                "import_rate": 0.07 if cheap else 0.30,
                "export_rate": 0.15,
                "standing": 0.57,
                "grid_import": 0.4,
                "grid_export": 0.0,
                "soc_start": 50.0,
            }
        )
    return out


def fake_api(url):
    """A tiny Kraken: one Octopus Go-like import (cheap 23:30-05:30 UTC), one flat export."""
    if "/products/?" in url:
        if "octopus" in url:
            return {
                "results": [
                    {
                        "code": "GO-VAR-22-10-14",
                        "display_name": "Octopus Go",
                        "direction": "IMPORT",
                        "brand": "OCTOPUS_ENERGY",
                    },
                    {
                        "code": "OUTGOING-VAR-24-10-26",
                        "display_name": "Outgoing Octopus",
                        "direction": "EXPORT",
                        "brand": "OCTOPUS_ENERGY",
                    },
                    {
                        "code": "PREPAY-VAR-18-09-21",
                        "display_name": "Key and Card",
                        "direction": "IMPORT",
                        "is_prepay": True,
                    },
                    {"code": "ZERO-IMPORT-60M", "display_name": "Zero", "direction": "IMPORT"},
                ],
                "next": None,
            }
        return {"results": [], "next": None}
    if url.endswith("/GO-VAR-22-10-14/") or url.endswith("/OUTGOING-VAR-24-10-26/"):
        code = "E-1R-" + url.rstrip("/").split("/")[-1] + "-A"
        return {
            "single_register_electricity_tariffs": {
                "_A": {"direct_debit_monthly": {"code": code, "standing_charge_inc_vat": 50.0}}
            }
        }
    if "standing-charges" in url:
        return {
            "results": [{"value_inc_vat": 50.0, "valid_from": "2020-01-01T00:00:00Z", "valid_to": None}],
            "next": None,
        }
    if "OUTGOING" in url:
        return {
            "results": [{"value_inc_vat": 12.0, "valid_from": "2020-01-01T00:00:00Z", "valid_to": None}],
            "next": None,
        }
    res = []
    for n in range(-1, 5):
        d0 = datetime(2026, 9, 19, tzinfo=UTC) + timedelta(days=n)
        res.append(
            {
                "value_inc_vat": 8.5,
                "valid_from": (d0 - timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
                "valid_to": (d0 + timedelta(hours=5, minutes=30)).isoformat().replace("+00:00", "Z"),
                "payment_method": None,
            }
        )
        res.append(
            {
                "value_inc_vat": 31.0,
                "valid_from": (d0 + timedelta(hours=5, minutes=30)).isoformat().replace("+00:00", "Z"),
                "valid_to": (d0 + timedelta(hours=23, minutes=30)).isoformat().replace("+00:00", "Z"),
                "payment_method": None,
            }
        )
    return {"results": res, "next": None}


def test_products_filtered():
    names = [p["code"] for p in kraken.products("octopus", fake_api)]
    assert names == ["GO-VAR-22-10-14", "OUTGOING-VAR-24-10-26"]


def test_rate_table_lookup_and_pattern():
    t = kraken.RateTable(
        kraken.rates(
            "octopus",
            "GO",
            "E-1R-GO-A",
            datetime(2026, 9, 19, tzinfo=UTC),
            datetime(2026, 9, 24, tzinfo=UTC),
            fetch=fake_api,
        )
    )
    assert t.at(datetime(2026, 9, 21, 2, 0, tzinfo=UTC)) == pytest.approx(0.085)
    assert t.at(datetime(2026, 9, 21, 12, 0, tzinfo=UTC)) == pytest.approx(0.31)
    assert t.at(datetime(2025, 1, 1, tzinfo=UTC)) is None
    assert t.pattern_at(datetime(2025, 1, 1, 12, tzinfo=UTC), UTC) == pytest.approx(0.31)


def test_scenarios_pair_imports_with_exports():
    cat = [
        {
            "supplier": "octopus",
            "code": "AGILE-24-10-01",
            "name": "Agile",
            "direction": "IMPORT",
            "tariff": "t1",
            "brand": "OCTOPUS_ENERGY",
        },
        {
            "supplier": "octopus",
            "code": "OUTGOING-VAR-24-10-26",
            "name": "Outgoing",
            "direction": "EXPORT",
            "tariff": "t2",
            "brand": "OCTOPUS_ENERGY",
        },
        {
            "supplier": "octopus",
            "code": "AGILE-OUTGOING-19-05-13",
            "name": "Agile Outgoing",
            "direction": "EXPORT",
            "tariff": "t3",
            "brand": "OCTOPUS_ENERGY",
        },
        {
            "supplier": "edf",
            "code": "EDF_EV_FIX_GOELEC_12M_V3_HH",
            "name": "Go Electric",
            "direction": "IMPORT",
            "tariff": "t4",
        },
    ]
    ids = [s["id"] for s in scenarios(cat)]
    assert ids == [
        "current",
        "octopus:AGILE-24-10-01+OUTGOING-VAR-24-10-26",
        "octopus:AGILE-24-10-01+AGILE-OUTGOING-19-05-13",
        "edf:EDF_EV_FIX_GOELEC_12M_V3_HH",
    ]


def test_car_moves_to_cheapest_half_hours():
    prices = [0.3, 0.07, 0.3, 0.07, 0.3]
    assert _shift_car(prices, [0, 0, 5.0, 0, 0], 3.7) == pytest.approx([0, 3.7, 0, 1.3, 0])


def test_actual_cost():
    a = actual_cost(day_records(DAYS[0]))
    assert a["standing"] == 0.57 and a["import_kwh"] == pytest.approx(19.2)


def test_overnight_run_end_to_end(tmp_path):
    store = SimStore(str(tmp_path))
    p = Params(capacity_kwh=10.0, max_charge_kw=4.0, max_discharge_kw=4.0)
    now = datetime(2026, 9, 23, 1, 30, tzinfo=UTC)
    steps = list(run(store, DAYS, lambda d: day_records(d), p, LON, now, 7.4, fetch=fake_api, log=lambda m: None))
    last = steps[-1]
    assert last["done"]
    s = store.summary
    assert s["days"] == 3 and [r["id"] for r in s["ranking"]][:1]
    ids = {r["id"] for r in s["ranking"]}
    assert ids == {"current", "octopus:GO-VAR-22-10-14+OUTGOING-VAR-24-10-26"}
    go = next(r for r in s["ranking"] if r["id"] != "current")
    assert go["standing"] == pytest.approx(1.5)  # 50p a day x 3
    # a second run the same night does nothing new
    again = list(run(store, DAYS, lambda d: day_records(d), p, LON, now, 7.4, fetch=fake_api, log=lambda m: None))
    assert not [x for x in again if x.get("stage") == "day"]

    # rates were cached: no network needed now
    def no_net(url):
        raise AssertionError(url)

    list(
        run(
            store,
            DAYS,
            lambda d: day_records(d),
            p,
            LON,
            now + timedelta(hours=1),
            7.4,
            fetch=no_net,
            log=lambda m: None,
        )
    )


def test_new_products_reported_after_first_night(tmp_path):
    store = SimStore(str(tmp_path))
    p = Params(capacity_kwh=10.0)
    now = datetime(2026, 9, 23, 1, 30, tzinfo=UTC)
    first = list(run(store, DAYS, day_records, p, LON, now, 7.4, fetch=fake_api, log=lambda m: None))[-1]
    assert first["new_products"] == []  # the first catalogue is the starting point
    store.catalogue["seen"].remove("octopus:GO-VAR-22-10-14")
    later = list(
        run(store, DAYS, day_records, p, LON, now + timedelta(days=1), 7.4, fetch=fake_api, log=lambda m: None)
    )[-1]
    assert [x["code"] for x in later["new_products"]] == ["GO-VAR-22-10-14"]


def test_opportunities_need_enough_days_and_saving():
    rows = [
        {"id": "current", "per_month": 60.0, "total": 30.0},
        {"id": "x", "per_month": 50.0, "total": 25.0},
        {"id": "y", "per_month": 58.0, "total": 29.0},
    ]
    assert [o["id"] for o in opportunities({"days": 15, "ranking": rows})] == ["x"]
    assert opportunities({"days": 5, "ranking": rows}) == []


def test_summary_ranks_and_compares():
    res = {
        "current": {
            "days": {
                d: {"cost": 2.0, "import_kwh": 1, "export_kwh": 0, "export_income": 0, "standing": 0.5} for d in DAYS
            }
        },
        "x": {
            "days": {
                d: {"cost": 1.0, "import_kwh": 1, "export_kwh": 0, "export_income": 0, "standing": 0.5} for d in DAYS
            }
        },
    }
    s = summarise(res, {d: {"cost": 2.5} for d in DAYS}, {}, DAYS)
    assert [r["id"] for r in s["ranking"]] == ["x", "current"]
    assert s["ranking"][0]["vs_current_month"] == pytest.approx(-30.44)
    assert s["actual_total"] == 7.5
