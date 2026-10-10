"""The report a run leaves behind, in a form that is cheap to read: `report.md` (the scoreboard and what changed against
the baseline) and `report.json` (the same, with each day's mode changes and each re-plan's timeline in a few words).
The full per-run detail the GUI draws from stays in the work folder's `runs/`; this is what a person or Claude opens."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import scoreboard

SHORT = {"self_use": "self", "hold": "hold", "charge": "chg", "export": "exp", "event": "event", "free": "free"}


def _hm(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%H:%M")


def plan_line(plan: dict) -> str:
    """One re-plan in one line: when, why, and the first steps of the timeline as `HH:MM mode level->level`."""
    steps = [f"{_hm(i[1])} {SHORT.get(i[0], i[0])} {i[3]:.0f}->{i[4]:.0f}" for i in plan["timeline"][:8]]
    more = f" (+{len(plan['timeline']) - 8})" if len(plan["timeline"]) > 8 else ""
    return f"{_hm(plan['at'])} [{plan['because']}] " + "; ".join(steps) + more


def condensed(result: dict) -> dict:
    if result.get("status") != "ok":
        return {"day": result.get("day"), "status": result.get("status"), "reason": result.get("reason")}
    s = result["series"]
    return {
        "day": result["day"],
        "status": "ok",
        "score": result["score"],
        "mode_changes": s["changes"],
        "level_5min": [[t, lvl, mode] for t, lvl, mode, *_ in s["trace"]],
        "replans": [plan_line(p) for p in s["plans"]],
        "prices": [
            {
                "t": r["start"][11:16],
                "import_p": round(r["act"] * 100, 2),
                "export_p": round(r["exp"] * 100, 2),
                "solar_kwh": r["solar"],
                "house_kwh": r["house"],
                "car_kwh": r["car"],
                "event": bool(r.get("axle")),
                "free": bool(r.get("free")),
            }
            for r in s["rows"]
        ],
    }


def write_report(out: Path, variants: list[dict], results: dict[str, list[dict]], base: str | None = None) -> Path:
    """`variants`: Variant.as_dict() each; `results`: {variant name: [result per day]}."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    md = []
    names = list(results)
    base = base or (names[0] if names else None)
    for v in variants:
        res = results.get(v["name"], [])
        label = next((r.get("code") for r in res if r.get("code")), v.get("code") or "working tree")
        extra = {
            k: x
            for k, x in (("settings", {**v["settings"], **v["consts"]}), ("fresh", v["fresh"]), ("config", v["config"]))
            if x
        }
        md.append("```\n" + scoreboard.table(f"{v['name']}  [{label}]  {json.dumps(extra)}", res) + "\n```")
        if base and v["name"] != base and base in results:
            md.append(scoreboard.versus(results[base], res, v["name"]))
    (out / "report.md").write_text("\n\n".join(md) + "\n", encoding="utf-8")
    body = {
        "variants": [
            {
                **v,
                "code_label": next((r.get("code") for r in results.get(v["name"], []) if r.get("code")), None),
                "days": [condensed(r) for r in results.get(v["name"], [])],
            }
            for v in variants
        ]
    }
    (out / "report.json").write_text(json.dumps(body, separators=(",", ":")), encoding="utf-8")
    return out
