"""Shim for the Redwood Micro Components desk: WidgeX only runs check files inside its harness folder, so this loads the desk's
real checks from C:/Users/Andy/.widgex/.xylonActiveCosms/calhacks_hyphenate-redwood/checks/ledger_checks.py (that checkout's .env selects its ledger, books and policy)."""

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(r"C:/Users/Andy/.widgex/.xylonActiveCosms/calhacks_hyphenate-redwood")
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_spec = importlib.util.spec_from_file_location("desk_checks", _ROOT / "checks" / "ledger_checks.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

for _name in dir(_mod):
    if not _name.startswith("_") and callable(getattr(_mod, _name)):
        globals()[_name] = getattr(_mod, _name)
