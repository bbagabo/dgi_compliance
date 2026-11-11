"""Comparison of a value sent by a client with the stored one (v5.4.1).

The desk form sends every field back: dates as text ("2026-11-11 08:14:27.839585"), numbers sometimes as
text, empty values as "" or null. The database returns datetime objects and numbers. A raw `!=` therefore
saw a change where there was none: every 'Update' of a submitted normalized invoice from the desk form was
refused ("Champs fiscaux DGI non modifiables: custom_dgi_normalized_on")."""
import datetime

from frappe.utils import flt, get_datetime


def same_value(a, b) -> bool:
    if not a and not b:
        return True                     # None, "", 0: no value on both sides
    if not a or not b:
        return False
    if isinstance(a, (datetime.datetime, datetime.date)) or isinstance(b, (datetime.datetime, datetime.date)):
        try:
            return get_datetime(a) == get_datetime(b)
        except Exception:
            return False
    if isinstance(a, (int, float)) or isinstance(b, (int, float)):
        try:
            return flt(a) == flt(b)
        except Exception:
            return False
    return str(a) == str(b)
