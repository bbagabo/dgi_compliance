"""Small MCF-SFE helpers needed server side (same rules as the gateway's protocol module)."""
import re
from datetime import datetime

SIG_RE = re.compile(r"^[A-Z0-9]{24}$")
DT_RE = re.compile(r"^\d{14}$")
INVOICE_TYPES = ("FV", "FT", "EV", "ET", "FA", "EA")


def nid_of(mid: str) -> str:
    mid = (mid or "").strip()
    return mid.rsplit("-", 1)[0] if "-" in mid else mid


def qr_content(mid, sig, nif, dt) -> str:
    """Protocol chapter 5: RDCDEF01;{MID};{SIG};{NIF};{DT}"""
    return f"RDCDEF01;{mid};{sig};{nif};{dt}"


def format_sig(sig: str) -> str:
    s = (sig or "").replace("-", "").upper()
    return "-".join(s[i:i + 4] for i in range(0, len(s), 4)) if len(s) == 24 else s


def display_datetime(dt: str) -> str:
    """AAAAMMJJHHmmss -> jj/MM/aaaa HH:mm:ss (format of the protocol invoice example)."""
    try:
        return datetime.strptime(dt, "%Y%m%d%H%M%S").strftime("%d/%m/%Y %H:%M:%S")
    except (TypeError, ValueError):
        return dt or ""


def parse_device_datetime(dt: str):
    try:
        return datetime.strptime((dt or "").strip(), "%Y%m%d%H%M%S")
    except ValueError:
        return None
