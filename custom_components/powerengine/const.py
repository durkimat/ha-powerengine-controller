"""Names shared by the PowerEngine integration."""

DOMAIN = "powerengine"

CONF_SETTINGS_DIR = "settings_dir"
CONF_MODE = "mode"
MODE_LIVE, MODE_SHADOW = "live", "shadow"   # shadow: beside the live PowerEngine, on its own copy, changing nothing
DEFAULT_SETTINGS_FOLDER = "powerengine"     # under Home Assistant's config folder, where the app keeps config.yaml
SHADOW_FOLDER = "powerengine_shadow"        # the shadow's own copy of it

PLATFORMS = ["sensor", "binary_sensor", "switch", "button"]

# the engine's status, as the integration's own sensor shows it
STATUS_STOPPED = "stopped"
STATUS_STARTING = "starting"
STATUS_RUNNING = "running"
STATUS_RESTARTING = "restarting"
STATUS_BACKING_OFF = "backing_off"          # it stopped on its own: starting again after a wait
STATUS_NEEDS_RESTART = "needs_restart"      # engine and integration speak different protocols: restart Home Assistant
STATUS_FAILED = "failed"
STATUSES = [STATUS_STOPPED, STATUS_STARTING, STATUS_RUNNING, STATUS_RESTARTING, STATUS_BACKING_OFF,
            STATUS_NEEDS_RESTART, STATUS_FAILED]

# Repairs
ISSUE_PROTOCOL = "engine_protocol"
ISSUE_CRASHING = "engine_crashing"
ISSUE_FILES = "engine_files_missing"
ISSUE_NO_SOURCE = "shadow_no_source"

SERVICE_RESTART_ENGINE = "restart_engine"
SERVICE_REFRESH_SHADOW = "refresh_shadow"

# the engine's exit statuses (apps/powerengine/engine_main.py)
EXIT_STOPPED, EXIT_PROTOCOL, EXIT_SECRET, EXIT_UNAVAILABLE = 0, 10, 11, 12
