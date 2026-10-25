"""The suite describes the first desk (Halyard), whatever company this checkout's .env selects.

This runs before any project module is imported, and ledgerdb loads the checkout's .env with
setdefault only, so these win. The desk home points at a scratch folder so no test touches a real
desk's ledger, mail or tokens.
"""

import os
import tempfile

os.environ["DESK_WORLD"] = "halyard"
os.environ["DESK_SLUG"] = "revenue-desk"
os.environ["DESK_HOOK_PREFIX"] = "hook-rd"
os.environ["DESK_STRUCTURE"] = "structure"
# Never let a test touch a real desk's live workflows: no external harness folder, no prefix, the default shim.
os.environ["DESK_HARNESS_DIR"] = ""
os.environ["DESK_HARNESS_FILE_PREFIX"] = ""
os.environ["DESK_CHECKS_SHIM"] = "checks/ledger_checks.py"
os.environ["ODOO_DB"] = os.environ.get("TEST_ODOO_DB", "revenue_test")
os.environ["COMPANY_NAME"] = "Halyard Marine Supply"
os.environ["REVENUE_DESK_HOME"] = tempfile.mkdtemp(prefix="revenue-desk-test-")
