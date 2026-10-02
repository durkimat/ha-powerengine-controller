# PowerEngine: demo mode and easier install

2026-09-29 · Matthew

> **Status (2 Oct 2026). Read this first.** This is a plan document converted from a claude.ai doc. Implementation
> details are in CLAUDE.md under "Demo mode plan".
> - **Done:** A1 and B1 (0.9.62); B2, C1, C2 and C3 (0.9.63); C4, the clean-install test on a fresh HA 2026.9.4 with
>   AppDaemon add-on 0.19.2. Its fixes shipped in 0.9.69–0.9.70: Task timer handles, lossless direct attribute writes, the demo
>   clock pinned to Europe/London, a quiet start-up, and the dashboard reloading after a demo change.
> - **Parked:** A2 (the setup card creates the dashboard), A3 (the install guide rewrite) and D1 (public beta). C4 showed the
>   install is still too long for a demo (about ten steps). The options are in issue #192: a no-install browser demo,
>   trimming the HA install (A2 and bundled chart cards), a one-command installer, or the native integration (#177).

## Goal

Someone with Home Assistant installs PowerEngine and sees it working on realistic data in minutes. They need no inverter, no MQTT broker and no configuration, and nothing in their home is ever controlled.

Three principles guide the work:

- **Nothing is written in demo mode.** No write reaches any device, by design and by a second check.
- **Demo parts are built as adapters.** They sit beside the real ones, so the demo reuses the same planner, dashboards and cards as real use.
- **Every change reduces the dependence on AppDaemon.** All access to Home Assistant goes through the adapter interfaces, so a native Home Assistant integration stays possible later without redoing this work.

## What someone installs

A demo user goes from about eight manual steps to three: install HACS, install the AppDaemon add-on, add one HACS repository. The PowerEngine setup card does the rest.

| Piece | Full install today | Demo target |
|---|---|---|
| HACS | Needed | Needed |
| AppDaemon add-on, with HACS AppDaemon discovery on | Needed | Needed (the one remaining dependency) |
| MQTT broker and integration | Needed | Not needed: entities are created directly |
| PowerEngine app (HACS) | Installed by hand | Installed by the setup card's button |
| PowerEngine card bundle (HACS) | Installed by hand | Installed by hand: the entry point |
| ApexCharts and Sunsynk Power Flow cards | Installed by hand | Installed by the setup card's button |
| Dashboard registration in configuration.yaml | Edited by hand | Created by the setup card (to be proved, step A2) |
| Handover package (update script, watchdogs) | Copied in by hand | Not needed |
| Inverter, tariff, charger and forecast integrations | Needed | Not needed |
| Config (mapping entities, settings) | Needed | Not needed |

The full install also gets easier: the same setup card checks and installs the shared pieces for real users too.

## Design

Demo mode swaps the edges of PowerEngine, not its middle. The planner, decisions, costs, dashboards and cards are the ones real users run. Only the adapters that read and write Home Assistant are replaced.

*(Diagram in the original: the same core and cards; only the adapters at the edges are swapped for demo ones (DemoWorld plus the gate), and the publisher gains a direct mode.)*

The demo adapters are new, and the publisher gains a direct mode that needs no MQTT. Everything else is what runs on your system today.

**1. Demo data pack.** Recorded days from a real home, scrubbed of account numbers, meter IDs and serials by the existing fixture builder, and shipped inside the app.

- Start with the replay night already in the tests. Add three or four more days: a sunny day, a winter day, an Axle event day and a car-charging day.
- Each day is shifted to today's date, so the plan and charts look current. The pack loops, and a picker on the welcome screen chooses the day.

**2. Demo adapters.** One for each adapter from steps 2–5.

- **Demo inverter.** It writes nothing. It simulates the battery's response, using the existing battery model, so the charge follows PowerEngine's own commands. The demo shows cause and effect, not a fixed replay.
- **Demo tariff, charger, forecast and events.** They read rates, smart slots, the car, solar and Axle events from the pack.
- **Second check.** In demo mode the app refuses every Home Assistant service call except publishing its own entities, and a test proves it.

**3. Direct publishing (no MQTT).** A publisher adapter with two versions:

- **MQTT:** as today, for real use.
- **Direct:** AppDaemon creates the entities itself, with the same names, so every dashboard works unchanged. The limits suit a demo: the entities are rebuilt on each start, and switches are changed through events rather than toggled on the tile.

**4. Setup card.** A new card in the card bundle, which can be placed on any dashboard.

- It checks each piece: the AppDaemon add-on and HACS's AppDaemon option, the PowerEngine app, the chart cards, and MQTT for full mode only.
- It installs what HACS can install with one button, using the same HACS connection as the Update button. For the rest it links to the right page.
- Last, it creates the PowerEngine dashboard (step A2).

**5. First run and presentation.**

- With no configuration, PowerEngine opens on a welcome screen with two choices: **Set up your system** and **Try the demo**.
- In the demo a banner runs across every page: *Demo: recorded data from a real home, nothing is controlled*. It carries **Choose a day** and **Exit demo**.
- Settings still work in the demo, so people can see what each one changes.

## Phases and steps

Ten steps in four phases, each a small release checked by the replay tests, like Phase 0. A first installable demo arrives at step C3, about eight sessions in. Progress on 29 Sep: A1 and B1 released in 0.9.62; B2, C1, C2 and C3 released in 0.9.63, the first working demo. Next: C4 (clean install on a fresh Home Assistant), A2 and A3.

| Step | What | Size | How it's checked |
|---|---|---|---|
| **A1** | Setup card: checks each piece and installs the app and chart cards via HACS | 1 session | Node tests; tried on your HA with a piece removed |
| **A2** | Setup card creates the PowerEngine dashboard, so no configuration.yaml edit is needed | 1–2 sessions, least certain | Prototype first, then decision 3 |
| **B1** | Publisher adapter: MQTT as today, plus direct publishing | 1 session | Replay unchanged; entities identical by name |
| **B2** | Switches and settings work through events when publishing directly | 1 session | Tests; the Config page saves in direct mode |
| **C1** | Demo data pack: builder script, 3–4 scrubbed days, date shifting | 1 session | Scrub check (no IDs); day totals match the source |
| **C2** | Demo adapters, with the simulated battery and the no-writes check | 1–2 sessions | A test that demo mode makes no service calls; the battery follows commands |
| **C3** | Welcome screen, demo banner, day picker, exit | 1 session | Dashboards rendered and checked, as for the waterfall |
| **C4** | Clean install on a fresh Home Assistant, following only the new guide | 1 session | A second HA in a container, from zero to demo |
| **A3** | INSTALL.md rewritten around the setup card, with demo and full paths | 1 session | Followed step by step in C4 |
| **D1** | Release as a public beta for the demo | — | After C4 passes |

The order is chosen so each step is useful on its own. The setup card (A1) helps your install straight away, and direct publishing (B1) removes a dependency before the demo needs it.

## Decisions for Matthew

Decided on 29 Sep 2026: the recommended answer in each case, as described below.

1. **Whose data?** Recommended: your recorded days, scrubbed. Identifiers are removed, but the days still show your household's usage pattern, car charging times and Axle events, and anyone who installs the demo could see them. The alternative is days generated from a model, which are safe but less convincing.
2. **Demo clock.** Recommended: real time, looping the chosen day. The alternative is speeding a day up (for example 10×, so a day plays in about 2.4 hours), which shows more but makes the charts jump.
3. **Creating the dashboard (A2).** Recommended: the setup card builds a normal UI dashboard from PowerEngine's generated layout. That removes the configuration.yaml step, but needs a way for the card to read the layout: a file under `/local`, which Home Assistant serves to any browser without a login. The fallback keeps today's one-line configuration.yaml entry. We'll prototype first and choose then.
4. **Settings in the demo.** Recommended: they can be changed and reset when leaving the demo, so people see what each one does. The alternative is read-only.

## Risks

The biggest risk is a demo that could touch a real device, and the design closes that off twice.

| Risk | What we do about it |
|---|---|
| A demo writes to a real inverter or charger | Demo adapters write nothing, the app refuses every service call in demo mode, and a test proves it |
| Your own system changes when you try the demo | The demo runs only when there's no real configuration, or as a separate app instance; your live PowerEngine is never switched into it |
| HACS changes its interface and the install buttons break | Every button has a link to do the same by hand, and C4 tests a clean install before release |
| HACS AppDaemon discovery is off by default | The setup card checks it and explains the one switch to turn on |
| The dashboard file under /local is readable without login | It holds only layout and PowerEngine's own entity names, never tokens or account details. If that's not acceptable, fall back to configuration.yaml (decision 3) |
| Directly created entities vanish when Home Assistant restarts | PowerEngine republishes them on start, which is fine for a demo; real use keeps MQTT |
| Work spreads into the AppDaemon shell | New demo code goes in pure modules and adapters, and the shell only wires them up |

## Later, not now

Three things this plan leaves for later, each made easier by it.

- **Native Home Assistant integration.** Replacing the AppDaemon shell would remove AppDaemon and MQTT for everyone. It would make installing one HACS click, and the demo would become an option in Home Assistant's own setup wizard. The adapters and publisher built here are the parts it needs. It's weeks of work, and it's in the backlog.
- **Public web demo.** A static page on GitHub Pages, built from the same demo data pack, for people without Home Assistant.
- **More demo days** (spring, a free-power day, a cold snap), each added to the pack by the builder script.
