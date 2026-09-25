import pathlib
import sys

# Make pe_core importable exactly as AppDaemon does (app folder on sys.path).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "apps" / "powerengine"))
