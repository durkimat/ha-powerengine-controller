"""Tests of the PowerEngine integration against a real Home Assistant (pytest-homeassistant-custom-component).

They run on custom_components/powerengine as committed (the files HACS installs, made current first), with the real
engine as its process. Run them with an environment that has Home Assistant (integration/requirements.txt):

    cd integration && python -m pytest
"""

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from build_integration import sync  # noqa: E402

sync()                                     # the generated files (engine/, protocol.py, ...) current, as CI also demands
sys.path.insert(0, str(ROOT))              # custom_components/ is at the repository root

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def _custom_integrations(request, socket_enabled):
    """The integration listens on a local socket for its engine, which the test plugin blocks by default. (A test that
    uses the recorder must have it before `hass` exists, so it comes first.)"""
    if "recorder_mock" in request.fixturenames:
        request.getfixturevalue("recorder_mock")
    request.getfixturevalue("enable_custom_integrations")
    yield
