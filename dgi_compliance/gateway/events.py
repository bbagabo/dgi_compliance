"""Append-only journal of the gateways (enrolment, approval, revocation, alerts, refusals)."""
import json

import frappe
from frappe.utils import now_datetime

LEVELS = ("Info", "Alerte", "Critique")


def log_event(gateway, code, message, *, level="Info", details=None, point_of_sale=None, device=None):
    try:
        if gateway and not point_of_sale:
            point_of_sale, device = frappe.db.get_value("DGI Gateway", gateway, ["point_of_sale", "mcf_device"]) or (None, None)
        doc = frappe.get_doc({
            "doctype": "DGI Gateway Event",
            "gateway": gateway if gateway and frappe.db.exists("DGI Gateway", gateway) else None,
            "point_of_sale": point_of_sale, "mcf_device": device,
            "event_code": (code or "INFO")[:60], "level": level if level in LEVELS else "Info",
            "message": (message or "")[:1000], "occurred_on": now_datetime(),
            "details": json.dumps(details or {}, ensure_ascii=False, default=str)[:20000],
        })
        doc.insert(ignore_permissions=True)
        if level != "Info":
            notify(f"[DGI] {code} - {gateway or point_of_sale or ''}", message)
        return doc.name
    except Exception:
        frappe.log_error(title="DGI Gateway Event", message=frappe.get_traceback())
        return None


def notify(subject, message):
    """Alert the DGI Fiscal Managers (falls back to the e-mail list of DGI Compliance Settings).

    v5.4.2: a server message left by the e-mail (e.g. "Please setup default outgoing Email Account") is never
    shown to the person whose action raised the alert: only the messages of the action itself go back."""
    shown = len(getattr(frappe.local, "message_log", None) or [])
    try:
        from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings
        settings = get_settings()
        recipients = [e.strip() for e in (settings.get("notify_recipients") or "").replace(";", ",").split(",") if e.strip()]
        users = frappe.get_all("Has Role", filters={"role": "DGI Fiscal Manager", "parenttype": "User"}, pluck="parent")
        recipients += [u for u in users if u and "@" in u and u not in recipients]
        for user in users:
            frappe.get_doc({"doctype": "Notification Log", "for_user": user, "type": "Alert",
                            "subject": subject[:140], "email_content": message}).insert(ignore_permissions=True)
        if recipients:
            frappe.sendmail(recipients=recipients, subject=subject, message=message, delayed=True)
    except Exception:
        frappe.log_error(title="DGI notification", message=frappe.get_traceback())
    finally:
        try:
            del frappe.local.message_log[shown:]
        except Exception:
            pass
