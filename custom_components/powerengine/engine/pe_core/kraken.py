"""Public tariff data from Kraken supplier APIs (Octopus, EDF): products, tariff codes and historical rates.

No login needed. All requests are GETs with a timeout; callers cache results (Simulator, overnight), so a run
makes a handful of requests, never a stream. `fetch_json` is injectable so the parsing is testable offline.
"""

from __future__ import annotations

import bisect
import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

SUPPLIERS = {
    "octopus": "https://api.octopus.energy/v1",
    "edf": "https://api.edfgb-kraken.energy/v1",
}
TIMEOUT_S = 20
MAX_PAGES = 40
USER_AGENT = "PowerEngine (Home Assistant AppDaemon app; tariff simulator)"
# Not tariffs a household can simply move to (new-build bundles, staff-only, test products).
EXCLUDE_WORDS = ("TEST", "ZERO-", "EMPLOYEE", "ENGAGE_EV")


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:        # noqa: S310 (fixed https hosts)
        return json.loads(resp.read().decode("utf-8"))


def _pages(url: str, fetch) -> list[dict]:
    out: list[dict] = []
    for _ in range(MAX_PAGES):
        data = fetch(url)
        out += data.get("results") or []
        url = data.get("next")
        if not url:
            break
    return out


def products(supplier: str, fetch=fetch_json) -> list[dict]:
    """Household electricity products a customer could choose now: import and export, no prepay or restricted."""
    items = _pages(f"{SUPPLIERS[supplier]}/products/?is_business=false", fetch)
    out = []
    for p in items:
        code = str(p.get("code", ""))
        text = (code + " " + str(p.get("display_name", ""))).upper()
        if p.get("is_prepay") or p.get("is_restricted") or p.get("is_business"):
            continue
        if "PAYG" in text or code.endswith("_PP") or any(w in text for w in EXCLUDE_WORDS):
            continue
        out.append({"supplier": supplier, "code": code, "name": p.get("display_name") or code,
                    "direction": p.get("direction") or "IMPORT", "is_variable": bool(p.get("is_variable")),
                    "is_tracker": bool(p.get("is_tracker")), "brand": p.get("brand"),
                    "description": (p.get("description") or "")[:200]})
    return out


def tariff_code(supplier: str, product: str, region: str, fetch=fetch_json) -> tuple[str | None, float | None]:
    """(electricity tariff code for the region, standing charge p/day inc VAT) or (None, None)."""
    d = fetch(f"{SUPPLIERS[supplier]}/products/{urllib.parse.quote(product)}/")
    t = ((d.get("single_register_electricity_tariffs") or {}).get(f"_{region}") or {})
    dd = t.get("direct_debit_monthly") or next(iter(t.values()), None) or {}
    return dd.get("code"), dd.get("standing_charge_inc_vat")


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def rates(supplier: str, product: str, tariff: str, start: datetime, end: datetime, kind: str = "standard-unit-rates",
          fetch=fetch_json) -> list[dict]:
    """[{"from": iso, "to": iso|None, "p": pence inc VAT}] oldest first; direct-debit prices only."""
    url = (f"{SUPPLIERS[supplier]}/products/{urllib.parse.quote(product)}/electricity-tariffs/"
           f"{urllib.parse.quote(tariff)}/{kind}/?period_from={_iso(start)}&period_to={_iso(end)}&page_size=1500")
    out = []
    for r in _pages(url, fetch):
        if r.get("payment_method") not in (None, "DIRECT_DEBIT"):
            continue
        if r.get("value_inc_vat") is None or not r.get("valid_from"):
            continue
        out.append({"from": r["valid_from"], "to": r.get("valid_to"), "p": float(r["value_inc_vat"])})
    return sorted(out, key=lambda r: r["from"])


def _parse(t: str | None) -> datetime | None:
    if not t:
        return None
    return datetime.fromisoformat(t.replace("Z", "+00:00"))


class RateTable:
    """Look up the price (GBP/kWh) at a moment from a list of rate windows."""

    def __init__(self, windows: list[dict]):
        self.w = sorted(((_parse(r["from"]), _parse(r.get("to")), r["p"] / 100) for r in windows),
                        key=lambda x: x[0])
        self.starts = [a for a, _, _ in self.w]

    def at(self, t: datetime) -> float | None:
        """The latest window starting at or before t that still covers t."""
        i = bisect.bisect_right(self.starts, t) - 1
        for j in range(i, max(-1, i - 50), -1):
            a, b, v = self.w[j]
            if b is None or t < b:
                return v
        return None

    def first_day(self) -> datetime | None:
        return self.w[0][0] if self.w else None

    def pattern_at(self, t: datetime, tz) -> float | None:
        """For dates before the tariff's first published rate: the price at the same local time on its first
        full day (a fixed or time-of-use tariff applied to an earlier day)."""
        first = self.first_day()
        if first is None:
            return None
        lt = t.astimezone(tz)
        base = (first.astimezone(tz) + timedelta(days=1)).date()
        probe = datetime.combine(base, lt.time(), tzinfo=tz)
        return self.at(probe.astimezone(timezone.utc))
