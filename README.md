# PowerEngine

A Home Assistant energy controller for a solar + battery + EV home, running as a Home Assistant
integration. It decides when to charge,
hold, discharge or export the battery using tariff prices, solar forecasts, EV
smart-charge slots and VPP events, and explains every decision in plain English.

> **Status: beta (0.1.x), Passive only.** PowerEngine monitors and explains your
> system and has its own dashboard. It does not control any device yet.

## Install

Follow the **[installation guide](docs/INSTALL.md)**: HACS, the PowerEngine dashboard, configuration, updating,
rollback, uninstalling and troubleshooting, with a check at each step. Updating restarts only the engine, not Home
Assistant. See **[docs/INTEGRATION.md](docs/INTEGRATION.md)** for how the
integration works.

A fresh install runs in **Passive** mode: it monitors, plans and simulates, but
never controls anything. Your settings live in
`/config/powerengine/config.yaml`, outside the integration's folder, so updates
never overwrite them.

## Development

```bash
pip install -r requirements-dev.txt
ruff check . && pytest -q
```

Decision logic lives in `apps/powerengine/pe_core/` and has no Home Assistant
dependency; `powerengine.py` wires it to Home Assistant through the host.

## Contributing

New inverters and other hardware are added as data where possible. See **[CONTRIBUTING.md](CONTRIBUTING.md)** for how to send a
device list from the setup wizard, write a definition, and open a pull request, and
[docs/WIZARD.md](docs/WIZARD.md) and [docs/INVERTERS.md](docs/INVERTERS.md) for the details.

## Licence

[Apache-2.0](LICENSE). Copyright 2026 Matthew Durkin. PowerEngine controls real equipment: use it at your own risk, and run the
supervised tests on your own inverter and firmware before letting it control anything. The licence disclaims all warranty.
