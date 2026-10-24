"""Shim: WidgeX only runs check files inside .widgex/harnesses/, so this re-exports the
real checks from <repo>/checks/ledger_checks.py. WidgeX runs it with the system python,
from this directory, with sys.path[0] set to the extension's own python/ folder."""

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_spec = importlib.util.spec_from_file_location("revenue_desk_checks", _ROOT / "checks" / "ledger_checks.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

for _name in dir(_mod):
    if not _name.startswith("_") and callable(getattr(_mod, _name)):
        globals()[_name] = getattr(_mod, _name)
