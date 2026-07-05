"""Centralized persistent audit of every exchange with the DGI, in the DGI Exchange Log DocType,
with configurable retention (default 180 days = 6 months) and manual/scheduled purge.

v3.5: bulk purge (single DELETE instead of row-by-row) + payload size cap so a huge
request/response can never bloat the database."""
import frappe
from frappe.utils import add_to_date, now_datetime


_SECRET_KEYS = ("token", "authorization", "password", "secret", "jwt", "apikey", "api_key", "bearer")
_MAX_PAYLOAD_CHARS = 50_000  # ~50 KB per stored payload


def _redact(obj):
    """Recursively mask values whose key looks like a credential, before persisting a log."""
    if isinstance(obj, dict):
        return {k: ("***REDACTED***" if any(s in str(k).lower() for s in _SECRET_KEYS) else _redact(v))
                for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_redact(x) for x in obj]
    return obj


def _as_capped_json(obj):
    if obj is None:
        return None
    s = frappe.as_json(_redact(obj))
    if len(s) > _MAX_PAYLOAD_CHARS:
        s = s[:_MAX_PAYLOAD_CHARS] + '... [TRONQUE]'
    return s


def log_exchange(direction, payload=None, response=None, error=None,
                 status_code=None, method=None, url=None, reference_invoice=None):
    # `direction` is a free-text label (Data field): never let it block an exchange.
    direction = (str(direction) if direction is not None else "")[:60]
    try:
        frappe.get_doc({
            "doctype": "DGI Exchange Log",
            "direction": direction,
            "method": method,
            "url": url,
            "status_code": status_code,
            "reference_invoice": reference_invoice,
            "request_payload": _as_capped_json(payload),
            "response_payload": _as_capped_json(response),
            "error": (str(error)[:500] if error else None),
        }).insert(ignore_permissions=True)
    except Exception:
        frappe.log_error(title="DGI Exchange Log insert failed", message=frappe.as_json(
            {"direction": direction, "request": payload, "response": response, "error": error}))


def purge_old_logs(days: int | None = None) -> int:
    from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings
    settings = get_settings()
    days = int(days if days is not None else (settings.log_retention_days or 180))
    cutoff = add_to_date(now_datetime(), days=-days)
    n = frappe.db.count("DGI Exchange Log", {"creation": ["<", cutoff]})
    if n:
        # Bulk delete: DGI Exchange Log has no child tables / attachments, so a direct
        # SQL delete is safe and orders of magnitude faster than delete_doc per row.
        frappe.db.delete("DGI Exchange Log", {"creation": ["<", cutoff]})
    frappe.db.commit()
    return int(n)


@frappe.whitelist()
def purge_now(days: int | None = None) -> dict:
    frappe.only_for(["System Manager", "Accounts Manager"])
    n = purge_old_logs(days)
    return {"deleted": n}


def scheduled_purge():
    from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings
    settings = get_settings()
    if not settings.enabled:
        return
    purge_old_logs()
