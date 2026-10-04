# <Feature name> (issue #<n>)

> **Status (<date>).** Draft. Nothing built. (Update this box in each PR that lands a stage: what is built, what is not.)

Copy this file for a feature that is more than a small fix: a new adapter or device kind, a change to planning or
control, anything touching the inverter, the tariff logic or what the card shows. Small fixes need no plan. Delete
sections that don't apply, but keep "Principles check" and "Acceptance".

## Problem

What is wrong or missing today, for whom, in plain words. Evidence if there is any (a diagnostics export, a recorded day,
a cost figure). No solution here.

## Goals and non-goals

- Goal: what must be true when this is done.
- Non-goal: what this deliberately does not do (so it isn't added later by accident).

## Decided / open

- **Decided** (owner, <date>): choices already made, so they aren't reopened.
- **Open**: questions that need an answer before or during the build. Say who decides.

## Design

The approach, at the level of modules and data: which files in `pe_core/`, the app, the card, the dashboard, the
definition files. Name the alternatives considered and why they lost.

## Principles check

Answer each in a line, "n/a" where it doesn't apply. The rules themselves are in CLAUDE.md "Guardrails" and "Principles".

- Inverter writes: does it change how often or what we write? Write budget, damping, read-back and RAM refresh kept?
- Money: can it buy grid energy at a price the plan didn't count?
- Modes: does it touch Active / Passive / Pause, Backup, Off-Grid, "bump" or "boost"? (It must not.)
- Names: any supplier or device name in user text goes through the names map.
- Limits: published attributes stay under 16 KB; `map_catalogue` size checked if a role is added.
- Both repos: does the card need a section list entry, a detect entry, a `MIN_APP_VERSION` / `MIN_CARD_VERSION` bump?
- Existing behaviour: refactor (replay unchanged) or intended change (replay re-recorded, diff explained)?

## Acceptance

Observable checks that say it is done. Each should be a test, a replay or golden result, or something the owner can see
on the dashboard.

- [ ] ...

## Stages

Small PRs, each passing the replay unchanged unless it is meant to change behaviour. For each: what it adds, what it
leaves out, and how it is tested.

1. **S1** ...
2. **S2** ...

## Risks and rollback

What could go wrong on the live house, how we would notice, and how to turn it off (a setting, Passive, a revert).
