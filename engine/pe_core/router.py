"""Where each message goes: the rules that send a kind of message to channels, each channel's hours, and the messages
held until a channel's hours open.

Pure decisions and the record of what happened. The app makes the Home Assistant service calls (`_send` in
powerengine.py) and tells the Outbox the outcome. A message is something that happened (a kind, a key, a title and
text); a channel is a place it can go (HA's notification area, a phone, the Alexa speakers, WhatsApp). The config's
`rules` say which kinds go to which channels (pe_core/config.py parses them).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo

from .announce import _clock, allowed

KEEP_LOG = 60            # the last deliveries kept for the Config page

# what PowerEngine sends: kind -> (label, hours a held message is still worth sending)
KINDS: dict[str, tuple[str, float]] = {
    "health": ("Health problems", 24),
    "inputs": ("Inputs not working", 12),
    "axle": ("Grid event scheduled", 12),
    "free_power": ("Free-power sessions", 12),
    "daily": ("Daily summary", 12),
    "simulator": ("Tariff opportunities", 24),
    "grid_reminder": ("Grid event reminders", 2),
    "grid_summary": ("Grid event summary", 14),
}
SOURCE = "powerengine"
DEFAULT_HOLD_H = 12      # a held message from elsewhere (the family dashboard, an automation) that sets no limit


@dataclass
class Message:
    kind: str
    key: str
    title: str
    text: str
    speech: str | None = None        # what the speakers say (default: the title, then the text)
    chat: str | None = None          # what a chat gets (default: the speech, else the title in bold and the text)
    source: str = SOURCE
    label: str | None = None         # what the Config page calls this kind (kinds from elsewhere)
    expires: datetime | None = None  # held past this, it is dropped


def hold_hours(kind: str) -> float:
    return KINDS[kind][1] if kind in KINDS else DEFAULT_HOLD_H


def channels_for(model: dict, kind: str) -> list[str]:
    """The channels the rules send this kind of message to, in order, each once."""
    out: list[str] = []
    for rule in model.get("rules", []):
        if kind in rule["events"]:
            for name in rule["to"]:
                if name in model.get("channels", {}) and name not in out:
                    out.append(name)
    return out


def is_open(channel: dict, now: datetime, tz: tzinfo | None) -> bool:
    """Whether the channel may be used now: always when it has no hours, else from the weekday/weekend start to the
    end (minutes after midnight)."""
    h = channel.get("hours")
    return True if not h else allowed(now, tz, h["weekday_from"], h["weekend_from"], h["until"])


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def public_model(model: dict) -> dict:
    """The channels and rules as the Config page edits them: the hours as HH:MM text (24:00 for no end)."""
    channels = {}
    for name, ch in model.get("channels", {}).items():
        out = {k: v for k, v in ch.items() if k != "hours"}
        h = ch.get("hours")
        out["hours"] = None if not h else {"weekday_from": _hhmm(h["weekday_from"]),
                                           "weekend_from": _hhmm(h["weekend_from"]),
                                           "until": "24:00" if h["until"] >= 1440 else _hhmm(h["until"])}
        channels[name] = out
    return {"channels": channels, "rules": [dict(r) for r in model.get("rules", [])],
            "reminder_hours": list(model.get("reminder_hours", []))}


def kind_list(seen: dict) -> list[dict]:
    """Every kind of message the Config page can route: PowerEngine's own, then any seen from elsewhere."""
    out = [{"kind": k, "label": v[0], "source": SOURCE} for k, v in KINDS.items()]
    out += [{"kind": k, "label": v.get("label", k), "source": v.get("source", "external")}
            for k, v in sorted(seen.items()) if k not in KINDS and k != "test"]
    return out


def _held_note(held_at: datetime, now: datetime, tz: tzinfo | None) -> str:
    h = held_at.astimezone(tz) if tz else held_at
    n = now.astimezone(tz) if tz else now
    gap = (n.date() - h.date()).days
    day = "" if gap <= 0 else (" yesterday" if gap == 1 else f" on {h:%A}")
    return f"Held message from {_clock(h)}{day}."


def render(channel: dict, msg: Message, held_at: datetime | None = None, now: datetime | None = None,
           tz: tzinfo | None = None) -> tuple[str | None, str]:
    """(title, body) to send to this channel. The notification area and a phone get the title and the text; the speakers
    and chats get one text. The channel's opening words come first, then, for a held message, when it was held."""
    kind = channel["type"]
    note = _held_note(held_at, now or held_at, tz) if held_at else ""
    lead = [s for s in (channel.get("intro", ""), note) if s]
    if kind in ("bell", "notify"):
        return msg.title, " ".join(lead + [msg.text])
    if kind == "alexa":
        body = msg.speech if msg.speech is not None else f"{msg.title}. {msg.text}"
    else:
        body = msg.chat if msg.chat is not None else (
            msg.speech if msg.speech is not None else f"*{msg.title}*\n{msg.text}")
    return None, " ".join(lead + [body])


class Outbox:
    """What the router remembers: messages held for a channel, the kinds seen (so the Config page can list ones it
    was never told about) and the last deliveries. Kept on disk so a restart loses nothing."""

    def __init__(self, path: str | None = None):
        self.path = path
        self.held: list[dict] = []
        self.seen: dict[str, dict] = {}
        self.log: list[dict] = []
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    raw = json.load(fh)
                self.held = [h for h in raw.get("held", []) if isinstance(h, dict)]
                self.seen = dict(raw.get("seen", {}))
                self.log = [e for e in raw.get("log", []) if isinstance(e, dict)][-KEEP_LOG:]
            except (OSError, ValueError):
                pass

    def see(self, msg: Message, now: datetime) -> None:
        known = self.seen.get(msg.kind, {})
        label = KINDS[msg.kind][0] if msg.kind in KINDS else (msg.label or known.get("label") or msg.kind)
        changed = known.get("source") != msg.source or known.get("label") != label
        self.seen[msg.kind] = {"source": msg.source, "label": label, "last": now.isoformat(timespec="seconds")}
        if changed:
            self.save()

    def record(self, now: datetime, msg: Message, channel: str, outcome: str, note: str = "") -> None:
        """outcome: sent, held, released (a held one sent), skipped (outside the hours, not held), dropped (held too
        long) or failed."""
        self.log.append({"at": now.isoformat(timespec="seconds"), "kind": msg.kind, "title": msg.title[:80],
                         "channel": channel, "outcome": outcome, "note": note[:160]})
        self.log = self.log[-KEEP_LOG:]
        self.save()

    def hold(self, channel: str, msg: Message, now: datetime) -> None:
        expires = msg.expires or now + timedelta(hours=hold_hours(msg.kind))
        self.held = [h for h in self.held if not (h["channel"] == channel and h["key"] == msg.key)]
        self.held.append({"channel": channel, "key": msg.key, "kind": msg.kind, "title": msg.title, "text": msg.text,
                          "speech": msg.speech, "chat": msg.chat, "source": msg.source,
                          "held_at": now.isoformat(timespec="seconds"),
                          "expires": expires.isoformat(timespec="seconds")})
        self.save()

    def cancel(self, prefix: str) -> int:
        """The problem behind messages is over: forget the held ones whose key starts with prefix."""
        gone = [h for h in self.held if h["key"].startswith(prefix)]
        if gone:
            self.held = [h for h in self.held if h not in gone]
            self.save()
        return len(gone)

    def release(self, now: datetime, model: dict, tz: tzinfo | None) -> list[tuple[str, dict, Message, datetime]]:
        """The held messages to send now, as (channel name, channel, message, when held): at most one per channel each
        time (the speakers would talk over each other), from channels whose hours are open. Messages held too long, or
        for a channel that is gone, are dropped here."""
        out, used, keep = [], set(), []
        for h in self.held:
            ch = model.get("channels", {}).get(h["channel"])
            msg = Message(h["kind"], h["key"], h["title"], h["text"], h.get("speech"), h.get("chat"),
                          h.get("source", SOURCE))
            if ch is None:
                self.record(now, msg, h["channel"], "dropped", "that channel no longer exists")
            elif now >= datetime.fromisoformat(h["expires"]):
                self.record(now, msg, h["channel"], "dropped", "held too long to be worth sending")
            elif is_open(ch, now, tz) and h["channel"] not in used:
                used.add(h["channel"])
                out.append((h["channel"], ch, msg, datetime.fromisoformat(h["held_at"])))
            else:
                keep.append(h)
        self.held = keep
        self.save()
        return out

    def snapshot(self) -> dict:
        return {"seen": self.seen, "held": [{k: h[k] for k in ("channel", "kind", "title", "held_at", "expires")}
                                            for h in self.held], "log": self.log[-30:]}

    def save(self) -> None:
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"held": self.held, "seen": self.seen, "log": self.log}, fh)
        os.replace(tmp, self.path)
