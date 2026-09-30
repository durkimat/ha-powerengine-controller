"""A tiny lookup from (kind, name) to an adapter factory. Adapters register themselves when their module is imported.

Inverters can also be plain data: every definition file in `devices/` (`<name>.yml`, see docs/INVERTERS.md) is
registered under ("inverter", <name>) as a `DefinedInverter` factory, the first time an inverter is asked for. A
hand-written class registered under the same name (`SolisInverter`, the Solis definition with its usual
constructor) takes its place."""

from __future__ import annotations

from collections.abc import Callable

KINDS = ("inverter", "tariff", "event", "ev", "forecast")

_registry: dict[str, dict[str, Callable]] = {kind: {} for kind in KINDS}


def _check_kind(kind: str) -> None:
    if kind not in KINDS:
        raise ValueError(f"unknown adapter kind {kind!r}; expected one of {KINDS}")


def register(kind: str, name: str, factory: Callable) -> None:
    """Register `factory` (a zero-or-more-arg callable that builds an adapter) under (kind, name)."""
    _check_kind(kind)
    _registry[kind][name] = factory


_definitions_loaded = False


def _load_definitions() -> None:
    """Register the definition files in devices/ as inverters (once), unless a class already owns the name."""
    global _definitions_loaded
    if _definitions_loaded:
        return
    _definitions_loaded = True
    from . import solis  # noqa: F401  (registers SolisInverter under "solis")
    from .defined import defined_factory
    from .definition import available
    for name in available():
        _registry["inverter"].setdefault(name, defined_factory(name))


def get(kind: str, name: str) -> Callable:
    """The factory registered for (kind, name)."""
    _check_kind(kind)
    if kind == "inverter":
        _load_definitions()
    try:
        return _registry[kind][name]
    except KeyError:
        known = names(kind)
        raise KeyError(f"no {kind} adapter named {name!r}; known: {known or '(none registered)'}") from None


def names(kind: str) -> list[str]:
    """The names registered under `kind`."""
    _check_kind(kind)
    if kind == "inverter":
        _load_definitions()
    return sorted(_registry[kind])


__all__ = ["KINDS", "register", "get", "names"]
