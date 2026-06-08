"""Shared helpers for the e-DEF engine."""
from datetime import datetime
import frappe


def to_db_datetime(value):
    """Normalise an e-DEF ISO-8601 datetime into a MariaDB-safe 'YYYY-MM-DD HH:MM:SS' string.

    The DGI e-DEF API returns timezone-aware ISO timestamps such as
    '2026-06-08T16:04:42+01:00' or '2026-06-07T08:02:23.299Z'. MariaDB DATETIME columns reject
    the 'T' separator and the timezone offset, raising error 1292. This converts such values to a
    naive (wall-clock) datetime string. Returns None for empty/invalid input.
    """
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            try:
                dt = frappe.utils.get_datetime(s)
            except Exception:
                return None
    if getattr(dt, "tzinfo", None) is not None:
        dt = dt.replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d %H:%M:%S")
