"""The battery ledger: where the energy in the battery came from, and what it cost.

Every kWh charged is a lot tagged with its source (solar or grid) and its basis (GBP/kWh): the export rate for
solar (it could have been exported instead) and the *standard* rate for grid energy (any smart-slot saving has
already been credited when it was bought). Discharges use the oldest energy first (first in, first out). Losses
stay with the energy: taking `out` kWh uses `out / round-trip efficiency` kWh of lots.
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_LOTS = 400


@dataclass
class Lot:
    kwh: float          # as charged (before losses)
    basis: float        # GBP/kWh
    source: str         # "solar" | "grid"


class Ledger:
    def __init__(self, lots: list[dict] | None = None):
        self.lots: list[Lot] = [Lot(**x) for x in (lots or [])]

    # --- state -----------------------------------------------------------------------
    @property
    def kwh(self) -> float:
        return sum(x.kwh for x in self.lots)

    @property
    def value(self) -> float:
        return sum(x.kwh * x.basis for x in self.lots)

    def to_list(self) -> list[dict]:
        return [{"kwh": round(x.kwh, 5), "basis": round(x.basis, 5), "source": x.source} for x in self.lots]

    # --- changes ---------------------------------------------------------------------
    def add(self, kwh: float, source: str, basis: float) -> None:
        if kwh <= 1e-6:
            return
        last = self.lots[-1] if self.lots else None
        if last and last.source == source and abs(last.basis - basis) < 1e-4:
            last.kwh += kwh
        else:
            self.lots.append(Lot(kwh, basis, source))
        if len(self.lots) > MAX_LOTS:                       # merge the two oldest lots (weighted basis)
            a, b = self.lots[0], self.lots[1]
            if a.source == b.source:
                tot = a.kwh + b.kwh
                self.lots[1] = Lot(tot, (a.kwh * a.basis + b.kwh * b.basis) / tot if tot else a.basis, a.source)
                del self.lots[0]

    def take(self, out_kwh: float, rte: float, fallback_basis: float) -> list[Lot]:
        """Remove energy for `out_kwh` delivered; returns the lots used (in charged kWh).

        If the ledger runs dry (it started empty, or the meters disagree), the rest comes from an 'unknown' grid
        lot at `fallback_basis`.
        """
        need = out_kwh / rte if rte > 0 else out_kwh
        used: list[Lot] = []
        while need > 1e-9 and self.lots:
            lot = self.lots[0]
            part = min(lot.kwh, need)
            used.append(Lot(part, lot.basis, lot.source))
            lot.kwh -= part
            need -= part
            if lot.kwh <= 1e-9:
                self.lots.pop(0)
        if need > 1e-9:
            used.append(Lot(need, fallback_basis, "grid"))
        return used

    def reconcile(self, actual_kwh: float, fallback_basis: float) -> None:
        """Match the ledger to the energy really in the battery (in charged kWh): trim the oldest, or add unknown."""
        diff = actual_kwh - self.kwh
        if diff > 1e-3:
            self.lots.insert(0, Lot(diff, fallback_basis, "grid"))
        elif diff < -1e-3:
            self.take(-diff, 1.0, fallback_basis)
