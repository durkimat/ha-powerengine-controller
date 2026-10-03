# Plans

Design documents, converted from the owner's claude.ai docs on 2 Oct 2026. Each starts with a **Status** box saying
what has been built since. Read the relevant plan before working on its area. CLAUDE.md stays the source of truth for
rules and for what the code does now, and GitHub issues are the backlog.

| Plan | Covers | Read when |
|---|---|---|
| [making-it-generic.md](making-it-generic.md) | Adapters, plant definitions, firmware variants, RAM vs EEPROM (UK survey), contribution workflow, setup wizard, safety, roadmap phases 0–3 | Adding an inverter, tariff or charger, Phase 1 work, the low-write mode (#189), the setup wizard, the licence |
| [low-write-mode.md](low-write-mode.md) | Protecting EEPROM-only inverters: what a write is, evidence from the demo days (`tools/low_write_study.py`), options (overnight cycle, write credit, derived arbitrage band), stages L0 to L4 | The low-write mode (#189), write budgets, arbitrage on timed windows |
| [multiple-devices.md](multiple-devices.md) | More than one inverter, battery, car, charger, tariff or grid-event provider: devices as capability sets, a plan per battery, per-device control, stages M0 to M3 | Anything that adds a second device of a kind |
| [demo-and-easier-install.md](demo-and-easier-install.md) | Demo mode design, install simplification, steps A1–D1, decisions, risks | Demo or install work (#192), the native integration (#177) |

Keep these up to date: when a step lands, update the plan's Status box in the same PR.
