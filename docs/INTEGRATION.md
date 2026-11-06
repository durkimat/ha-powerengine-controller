# PowerEngine as a Home Assistant integration

PowerEngine runs as a Home Assistant **integration**. The integration is thin and
rarely changes; the engine that plans and controls is a separate process that it starts and watches. So:

- PowerEngine's entities are real Home Assistant entities (`sensor.pe_...`);
- updating PowerEngine in HACS restarts **only the engine**, within a minute, and not Home Assistant. Home Assistant
  needs restarting only when a Repair says so (the integration and engine have changed how they talk);
- the engine keeps its settings and data in `/config/powerengine`, and updates never touch them.

It works wherever Home Assistant runs its own Python (OS, Supervised, Container, Core). Tested against Home Assistant
2026.10. [INSTALL.md](INSTALL.md) is the full guide: install, the dashboard, configuration and first-day checks. The rest of
this page covers updating.

## Updating

HACS shows the update as for any integration. Download it: the engine restarts by itself within a minute (the
PowerEngine device's Engine sensor says when), and a Repair says if Home Assistant needs a restart too. The Restart
engine button and `powerengine.restart_engine` do it at once. Read the release notes' **Behaviour changes** first.
