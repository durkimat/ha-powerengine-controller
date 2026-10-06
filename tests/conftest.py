import pathlib
import sys

# Make pe_core importable exactly as AppDaemon does (app folder on sys.path).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "apps" / "powerengine"))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def his_words_after_each_test():
    """The app sets the module's names map (pe_core.names) when it starts, and a demo-app test leaves it set, which
    made later tests that read his words (Axle, Zappi, ...) fail depending on how the run was split. Put the defaults
    back after every test."""
    yield
    from pe_core.names import set_current
    set_current(None)
