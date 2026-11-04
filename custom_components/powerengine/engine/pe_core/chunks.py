"""Publishing a list too big for one sensor's attributes on a few sensors.

HA's recorder drops a sensor's attributes from history over 16384 bytes. The input and settings catalogues are
lists, so when one outgrows a single sensor it is split in order across numbered sensors (`map_catalogue`,
`map_catalogue_2`, ...). The first sensor carries `parts` (how many) and the card joins the lists back together."""
import json

SPLIT_AT = 15000            # a part stays under this many bytes (HA's limit is 16384; the diagnostics warn at 15000)
MAX_PARTS = 4               # numbered sensors that exist per list


def size(attrs) -> int:
    """Bytes as HA's recorder stores them: compact JSON, UTF-8."""
    return len(json.dumps(attrs, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def part_key(key: str, n: int) -> str:
    """Entity key of part `n` (1 is the original sensor)."""
    return key if n == 1 else f"{key}_{n}"


def split(attrs: dict, limit: int = SPLIT_AT) -> list[dict]:
    """The attributes as one dict when they fit, else several in order: every list is cut between its items, every
    part has all the list keys, and part 1 also holds the other keys and `parts`."""
    if size(attrs) <= limit:
        return [attrs]
    lists = [k for k, v in attrs.items() if isinstance(v, list)]
    parts = [{**{k: v for k, v in attrs.items() if k not in lists}, **{k: [] for k in lists}}]
    for k in lists:
        for item in attrs[k]:
            parts[-1][k].append(item)
            if size(parts[-1]) > limit and len(parts[-1][k]) > 1:
                parts[-1][k].pop()
                parts.append({x: [] for x in lists})
                parts[-1][k].append(item)
    parts[0]["parts"] = len(parts)
    return parts


def join(parts: list[dict]) -> dict:
    """The inverse of `split` (the card does this in JS)."""
    out = dict(parts[0])
    out.pop("parts", None)
    for p in parts[1:]:
        for k, v in p.items():
            if isinstance(v, list):
                out[k] = out.get(k, []) + v
    return out
