"""Tests of the PowerEngine integration against a real Home Assistant (pytest-homeassistant-custom-component).

They run on the *built* integration (tools/build_integration.py), the same files HACS would install, with the real
engine as its process. Run them from the repository root with an environment that has Home Assistant:

    python -m pytest integration/tests
"""

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from build_integration import build  # noqa: E402

BUILD = build(ROOT / "integration" / "build")
sys.path.insert(0, str(BUILD))

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def _custom_integrations(request, socket_enabled):
    """The integration listens on a local socket for its engine, which the test plugin blocks by default. (A test that
    uses the recorder must have it before `hass` exists, so it comes first.)"""
    if "recorder_mock" in request.fixturenames:
        request.getfixturevalue("recorder_mock")
    request.getfixturevalue("enable_custom_integrations")
    yield
