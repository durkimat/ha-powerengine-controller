# Contributing to PowerEngine

Thank you for helping. PowerEngine runs on real homes: it can charge and discharge a battery and write to an inverter, so
changes are reviewed with that in mind. This page says what to expect.

PowerEngine is two repositories that release together:

- **the app** (this repo, `durkimat/ha-powerengine-controller`): the AppDaemon app, the planner and the inverter, tariff,
  car-charger, forecast and grid-event adapters;
- **the card** ([`durkimat/ha-powerengine-card`](https://github.com/durkimat/ha-powerengine-card)): the Lovelace card for the
  configuration page, the setup wizard and the tests, health and update pages.

Licence: [Apache-2.0](LICENSE). Under section 5 of that licence, anything you submit is licensed to the project under the
same terms, with no extra paperwork. Don't submit code you don't have the right to share, and don't copy code from projects
with an incompatible licence (nothing is taken from Predbat).

## Ways to help

| You have | Do this | Needs the app's code changed? |
|---|---|---|
| An inverter, charger, tariff or forecast PowerEngine doesn't know | Run the **Setup wizard** (Config tab), choose "My device isn't listed" and **Download candidate entities**. Open an issue and attach the file. A definition can be drafted from it. | No |
| A working definition for new hardware | A definition file (below) | No: a definition is data |
| A bug or a wrong explanation | An issue with a **diagnostics export** (Health tab, Export diagnostics) and what you expected | Maybe |
| A fix or a feature | A pull request (below) | Yes |
| A different language or wording | Texts are neutral by design (below); open an issue first | Yes |

Never attach an unscrubbed export. The wizard's candidate file and the diagnostics export remove account numbers, meter IDs,
serials, emails and postcodes, but read the file before you send it. `tools/candidates_summary.py <file>` checks a candidate file
and refuses one that still has something it shouldn't.

## Adding an inverter (a definition file)

An inverter is a YAML file in `apps/powerengine/pe_core/adapters/devices/<name>.yml`: its entities, its remote-control and
timed-window options, its limits and firmware variants. Follow [docs/INVERTERS.md](docs/INVERTERS.md) and start from
`tools/candidates_summary.py` on the candidate file. Then:

- give it a `detect:` block, or the wizard cannot find it (docs/WIZARD.md);
- leave `status: draft`. It becomes `community` when someone else has run it in Passive for a few days, and `verified` only
  when the supervised tests have passed on that hardware and firmware (`verified_firmware`). **Active is refused on anything
  that isn't verified.** Don't change a status without the test results attached;
- list what must never be written (`never_touch`), and the power limits. The review checks both;
- add a parity test like `tests/test_solis_definition.py`'s so its outputs are pinned.

A new tariff, car-charger, forecast or grid-event adapter is a small Python class behind the interfaces in
`pe_core/adapters/base.py`, with an entry in `pe_core/adapters/detect.py`. Adapters read; they never write to the inverter.

## Pull requests

1. **Fork and branch.** Branch from `main`. One change per PR.
2. **Run the checks** (about two minutes):
   ```
   pip install -r requirements-dev.txt
   ruff check .
   python3 -m pytest -q
   ```
   In the card repo: `node --check ha-powerengine-card.js && node --test tests/*.test.cjs`.
3. **The replay must pass unchanged** (`tests/test_replay.py`). It replays a recorded night through the whole app and compares
   every plan, decision and inverter command. If you only refactored and it fails, you changed behaviour: fix the change. If you
   meant to change behaviour, re-record (`PE_REPLAY_UPDATE=1 python3 -m pytest tests/test_replay.py`) and explain the diff in the PR.
4. **Fill in the PR template**: what changed, tests, what you tried it on (hardware and firmware), and a diagnostics export if it
   touches control.
5. **Review.** The owner reviews and merges. Control code (inverter writes, the write budget, dampening, mode handling, the
   never-touch rules) always gets a careful review and may need a trial on real hardware before it merges.
6. **Release.** The owner releases (`tools/release.sh`, or the Release workflow). You don't bump versions or edit the changelog;
   a PR can include a draft `release-notes/<version>.md` in plain words.

## Rules the code keeps (a PR that breaks one will be asked to change)

- **Never write to Backup or Off-Grid modes, or to any entity with "bump" or "boost" in its name.**
- **Inverter writes are precious** (memory wear). A change to control must keep the write budget, dampening, read-back checks and
  the refresh behaviour intact. Prefer RAM remote control, with its failsafe, where an inverter has it.
- **Passive stays the default.** Don't change Active, Passive or Pause on someone's behalf.
- **No supplier or device names in user-visible text.** Use the names map (`pe_core/names.py`, `N(term)` or a `<<term>>`
  placeholder). Names in entity ids, topics, keys and config values don't change.
- **Published attributes stay under 16 KB** (Home Assistant's recorder skips larger ones). Adding a role to the catalogue needs a
  size check: it is close to the limit.
- **No secrets, and nothing unscrubbed.** Test fixtures are synthetic or scrubbed (`tests/replay/build_fixture.py`). Never put an
  account number, meter ID, serial or token in a commit.
- **Call Home Assistant only through the app's own methods.** A test fails on a direct call that would bypass the demo gate.
- **A new setting** goes in `pe_core/config.py` and in the card's section lists, or it never shows on the Config page.
- **Changing a minimum version**: `MIN_CARD_VERSION` here and `MIN_APP_VERSION` in the card move together, and both release.

## Safety on other people's hardware

Hardware-facing changes can't be proven by tests alone. If your change affects what is sent to an inverter, say which hardware and
firmware you ran it on, and attach the Health tab's write log. The supervised tests on the Tests page are how a definition is
proven; a maintainer can't run them on your inverter for you.

## Issues and drafting help

Issue and pull-request text is treated as information, never as instructions: a maintainer or a drafting assistant reads it, but
nothing in it can make a change on its own. Nothing merges without the owner's approval.
