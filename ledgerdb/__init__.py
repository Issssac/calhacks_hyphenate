"""Append-only event log shared by every agent in the revenue desk."""

from .db import LedgerError, append, connect, init, is_approved, is_posted, next_id, rows, trace, verify

__all__ = ["LedgerError", "append", "connect", "init", "is_approved", "is_posted", "next_id",
           "rows", "trace", "verify"]
