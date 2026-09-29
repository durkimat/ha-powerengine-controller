"""A tiny lookup from (kind, name) to an adapter factory. Adapters register themselves when their module is imported."""

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


def get(kind: str, name: str) -> Callable:
    """The factory registered for (kind, name)."""
    _check_kind(kind)
    try:
        return _registry[kind][name]
    except KeyError:
        known = names(kind)
        raise KeyError(f"no {kind} adapter named {name!r}; known: {known or '(none registered)'}") from None


def names(kind: str) -> list[str]:
    """The names registered under `kind`."""
    _check_kind(kind)
    return sorted(_registry[kind])


__all__ = ["KINDS", "register", "get", "names"]
