# PowerEngine card

The dashboard cards for [PowerEngine](https://github.com/durkimat/ha-powerengine-controller): the configuration page
(input mappings, feature switches, safety margins), the plan, costs, health and test pages, and the update and setup
cards.

**This is part of PowerEngine, not a separate install.** The source is `ha-powerengine-card.js` here; the build
(`tools/build_integration.py`) puts a copy, with its version set to the engine's, in
`custom_components/powerengine/frontend/`, and the integration serves it and adds it to Lovelace's resources
(`custom_components/powerengine/frontend.py`). Installing PowerEngine from HACS as an integration is the whole install.
See [docs/INSTALL.md](../docs/INSTALL.md).

Tests: `node --check ha-powerengine-card.js && node --test tests/*.test.cjs` (CI runs them).
