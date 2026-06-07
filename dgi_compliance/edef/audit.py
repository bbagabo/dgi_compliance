"""Centralized persistent audit of every exchange with the DGI, in the DGI Exchange Log DocType,
with configurable retention (default 180 days = 6 months) and manual/scheduled purge."""
import frappe
from frappe.utils import add_to_date, now_datetime
from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings


def log_exchange(direction, payload=None, response=None, error=None,
                 status_code=None, method=None, url=None, reference_invoice=None):
    try:
        frappe.get_doc({
            "doctype": "DGI Exchange Log",
            "direction": direction,
            "method": method,
            "url": url,
            "status_code": status_code,
            "reference_invoice": reference_invoice,
            "request_payload": frappe.as_json(payload) if payload is not None else None,
            "response_payload": frappe.as_json(response) if response is not None else None,
            "error": (str(error)[:500] if error else None),
        }).insert(ignore_permissions=True)
    except Exception:
        frappe.log_error(title="DGI Exchange Log insert failed", message=frappe.as_json(
            {"direction": direction, "request": payload, "response": response, "error": error}))


def purge_old_logs(days: int | None = None) -> int:
    settings = get_settings()
    days = int(days if days is not None else (settings.log_retention_days or 180))
    cutoff = add_to_date(now_datetime(), days=-days)
    rows = frappe.get_all("DGI Exchange Log", filters={"creation": ["<", cutoff]}, pluck="name")
    for name in rows:
        frappe.delete_doc("DGI Exchange Log", name, ignore_permissions=True, force=True)
    frappe.db.commit()
    return len(rows)


@frappe.whitelist()
def purge_now(days: int | None = None) -> dict:
    frappe.only_for(["System Manager", "Accounts Manager"])
    n = purge_old_logs(days)
    return {"deleted": n}


def scheduled_purge():
    settings = get_settings()
    if not settings.enabled:
        return
    purge_old_logs()
