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


# Whole-app tests (replays, engine comparisons, closed loops): about 80% of the suite's time. `pytest -m "not slow"`
# skips them for a quick local check (tools/check.sh); CI and the release run everything.
SLOW_MODULES = {
    "test_replay", "test_compare_crosscheck", "test_engine_compare", "test_engine_v2_app", "test_engine_v2_evening",
    "test_history_records_app", "test_demo_app",
}


def pytest_collection_modifyitems(items):
    for item in items:
        if item.module.__name__.rsplit(".", 1)[-1] in SLOW_MODULES:
            item.add_marker(pytest.mark.slow)
