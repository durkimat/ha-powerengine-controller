"""New-version check straight from GitHub (HACS only looks every few hours).

Every few minutes PowerEngine reads the app's and the card's CHANGELOG.md from GitHub (raw files: no API rate
limit) and compares the newest version there with the one running. The sections for every newer version become the
"what's new" notes on the Configuration page; if there are many, the older ones become links to their releases.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request

OWNER = "durkimat"
APP_REPO = "ha-powerengine-controller"
CARD_REPO = "ha-powerengine-card"
TIMEOUT_S = 10
NOTES_BUDGET = 5000            # characters of notes in the sensor attributes (HA skips attributes over 16 KB)
USER_AGENT = "PowerEngine (Home Assistant AppDaemon app; version check)"

_HEADING = re.compile(r"^## (\d+\.\d+\.\d+)(?:\s.*)?$", re.M)


def raw_url(repo: str, path: str = "CHANGELOG.md", branch: str = "main") -> str:
    return f"https://raw.githubusercontent.com/{OWNER}/{repo}/{branch}/{path}"


def release_url(repo: str, version: str) -> str:
    return f"https://github.com/{OWNER}/{repo}/releases/tag/v{version}"


def fetch_text(url: str, etag: str | None = None) -> tuple[str | None, str | None]:
    """(text, etag); text is None when unchanged since `etag` (HTTP 304)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache",
                                               **({"If-None-Match": etag} if etag else {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:        # noqa: S310 - fixed https URL
            return resp.read().decode("utf-8", "replace"), resp.headers.get("ETag")
    except urllib.error.HTTPError as err:
        if err.code == 304:
            return None, etag
        raise


def vkey(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in v.strip().lstrip("v").split("."))
    except ValueError:
        return (0,)


def sections(changelog: str) -> dict[str, str]:
    """{version: its section's text (without the heading)} from a CHANGELOG with '## x.y.z' headings."""
    out: dict[str, str] = {}
    marks = list(_HEADING.finditer(changelog or ""))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(changelog)
        out[m.group(1)] = changelog[m.end():end].strip()
    return out


def _boring(card_text: str) -> bool:
    return not card_text or "no card changes" in card_text.lower()


def summary(running: str, app_log: str, card_log: str | None = None, budget: int = NOTES_BUDGET) -> dict:
    """What's newer than `running`: latest version, how many releases behind, and markdown notes (newest first)."""
    app, card = sections(app_log), sections(card_log or "")
    newer = sorted((v for v in app if vkey(v) > vkey(running)), key=vkey, reverse=True)
    latest = newer[0] if newer else (max(app, key=vkey) if app else None)
    parts, used, linked = [], 0, []
    for v in newer:
        body = app[v]
        if not _boring(card.get(v, "")):
            body += "\n\n**Card:** " + card[v].strip()
        block = f"### {v}\n\n{body}"
        if used + len(block) > budget and parts:
            linked.append(v)
            continue
        parts.append(block)
        used += len(block)
    if linked:
        parts.append("**Earlier releases:** " + ", ".join(f"[{v}]({release_url(APP_REPO, v)})" for v in linked))
    return {"running": running, "latest": latest, "behind": len(newer), "available": bool(newer),
            "notes": "\n\n".join(parts), "releases_url": f"https://github.com/{OWNER}/{APP_REPO}/releases"}
