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
os.environ["REVENUE_DESK_HOME"] = tempfile.mkdtemp(prefix="revenue-desk-test-")
