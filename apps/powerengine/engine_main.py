"""PowerEngine as its own process: connect to the Home Assistant integration, run the engine on it, stop when asked.

    PE_SECRET=... python engine_main.py --address tcp:127.0.0.1:PORT [--settings /config/powerengine/config.yaml]

The secret comes from the environment, not the command line, so it is not in the process list. Exit status: 0 stopped
when asked (or the integration went away for good), 10 the integration speaks another protocol version (a Home
Assistant restart is needed to update it), 11 the integration refused the secret, 12 no answer from the integration.
This file is the only place that joins the engine (powerengine.py) to the socket host (pe_core/host_socket.py); inside
Home Assistant the integration starts it (docs/plans/native-integration.md).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

EXIT_STOPPED, EXIT_PROTOCOL, EXIT_SECRET, EXIT_UNAVAILABLE = 0, 10, 11, 12


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PowerEngine, in its own process")
    parser.add_argument("--address", required=True, help="the integration's address: unix:/path or tcp:127.0.0.1:port")
    parser.add_argument("--settings", help="the settings file (default: the places PowerEngine looks)")
    parser.add_argument("--wait", type=float, default=60.0, help="seconds to wait for the integration at the start")
    options = parser.parse_args(argv)
    from pe_core import protocol
    try:
        protocol.parse_address(options.address)
    except ValueError as err:
        parser.error(str(err))

    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(message)s")
    secret = os.environ.get("PE_SECRET", "")
    if not secret:
        print("PE_SECRET is not set", file=sys.stderr)
        return EXIT_SECRET

    import powerengine

    from pe_core.host_socket import HostUnavailable, SocketHost

    host = SocketHost(options.address, secret, engine_version=powerengine.__version__,
                      args={"settings_file": options.settings} if options.settings else {})
    try:
        host.start(timeout=options.wait)
    except protocol.Refused as err:
        logging.error("The integration refused this engine: %s", err)
        return EXIT_PROTOCOL if err.reason == "protocol" else EXIT_SECRET
    except HostUnavailable as err:
        logging.error("%s", err)
        return EXIT_UNAVAILABLE

    import signal
    for name in ("SIGTERM", "SIGINT"):                       # stopped by the supervisor or a terminal: stop in order
        signal.signal(getattr(signal, name), lambda *_: host.stop_requested.set())

    engine = powerengine.Engine()
    engine.__dict__["_host"] = host
    try:
        engine.initialize()
        host.stop_requested.wait()
    finally:
        try:
            engine.terminate()
        finally:
            host.stop()
    if host.refused is not None:
        return EXIT_PROTOCOL if host.refused.reason == "protocol" else EXIT_SECRET
    return EXIT_STOPPED


if __name__ == "__main__":
    sys.exit(main())
