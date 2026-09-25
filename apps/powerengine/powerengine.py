"""PowerEngine AppDaemon entry point.

This is a thin adapter between AppDaemon/Home Assistant and pe_core. It must
stay small: decision logic belongs in pe_core so it can be tested offline.

v0.0.x is a scaffold: it loads and validates config and logs what it finds.
It never writes to the inverter or any other device.
"""

import appdaemon.plugins.hass.hassapi as hass

from pe_core import __version__
from pe_core.config import DEFAULT_PATHS, ConfigError, load_config


class PowerEngine(hass.Hass):
    def initialize(self):
        self.log(f"PowerEngine {__version__} starting (scaffold build: no control logic)")
        # Optional override. Not "config_path": AppDaemon sets that arg itself
        # (to the app's own YAML file), which 0.0.1 mistakenly read as settings.
        custom = self.args.get("settings_file")
        paths = [custom] if custom else list(DEFAULT_PATHS)
        try:
            cfg, used = load_config(paths)
        except ConfigError as err:
            self.log(f"Config problem: {err}. Running unconfigured; nothing will be controlled.", level="WARNING")
            return
        if cfg is None:
            self.log(f"No config.yaml found (looked in {', '.join(paths)}); running unconfigured.", level="WARNING")
            return
        self.log(f"Loaded config from {used}: {len(cfg.inputs)} inputs mapped, dry_run={cfg.dry_run}")
