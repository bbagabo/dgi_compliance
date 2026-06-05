"""Document events + scheduled token monitoring for DGI e-DEF compliance."""
import frappe
from frappe import _
from frappe.utils import nowdate, getdate, get_datetime, date_diff
from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings
from dgi_compliance.edef import client
from dgi_compliance.edef.mapper import build_invoice_request, validate_invoice_request

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _log(direction, payload, response, error=None):
    """Persistent audit trail via Error Log (always present in ERPNext, upgrade-safe)."""
    try:
        frappe.get_doc({
            "doctype": "Error Log",
            "method": f"dgi_compliance.edef[{direction}]",
            "error": frappe.as_json({"request": payload, "response": response, "error": error}),
        }).insert(ignore_permissions=True)
    except Exception:
        frappe.log_error(title="DGI e-DEF exchange", message=frappe.as_json(
            {"direction": direction, "request": payload, "response": response, "error": error}))


# ---------------- Sales Invoice events ----------------

def on_sales_invoice_submit(doc, method=None):
    settings = get_settings()
    if not settings.enabled:
        return

    dto = build_invoice_request(doc)
    errors = validate_invoice_request(dto)
    if errors:
        _mark_error(doc, "; ".join(errors))
        _log("validate", dto, None, errors)
        frappe.msgprint(_("Normalisation DGI: payload invalide - ") + "; ".join(errors))
        return

    created = client.create_invoice(dto)
    _log("create", dto, created.get("data"), created.get("error"))
    data = created.get("data") or {}
    if not created.get("ok") or not data.get("uid") or data.get("errorCode"):
        _mark_error(doc, data.get("errorDesc") or created.get("error") or "create failed")
        return

    uid, total, vtotal = data["uid"], data.get("total"), data.get("vtotal")
    doc.db_set("custom_dgi_uid", uid, update_modified=False)

    if not settings.auto_normalize:
        doc.db_set("custom_dgi_status", "Pending", update_modified=False)
        return

    confirmed = client.confirm_invoice(uid, total, vtotal)
    _log("confirm", {"uid": uid, "total": total, "vtotal": vtotal}, confirmed.get("data"), confirmed.get("error"))
    cdata = confirmed.get("data") or {}
    if not confirmed.get("ok") or cdata.get("errorCode") or not cdata.get("codeDEFDGI"):
        _mark_error(doc, cdata.get("errorDesc") or confirmed.get("error") or "confirm failed")
        return

    doc.db_set({
        "custom_dgi_status": "Normalized",
        "custom_dgi_code_def": cdata.get("codeDEFDGI"),
        "custom_dgi_counters": cdata.get("counters"),
        "custom_dgi_nim": cdata.get("nim"),
        "custom_dgi_datetime": cdata.get("dateTime"),
        "custom_dgi_qr_code": cdata.get("qrCode"),
        "custom_dgi_error": None,
    }, update_modified=False)


def on_sales_invoice_cancel(doc, method=None):
    settings = get_settings()
    if not settings.enabled:
        return
    uid = doc.get("custom_dgi_uid")
    if not uid:
        return
    res = client.cancel_invoice(uid)
    _log("cancel", {"uid": uid}, res.get("data"), res.get("error"))
    doc.db_set("custom_dgi_status", "Cancelled", update_modified=False)


def _mark_error(doc, msg):
    doc.db_set("custom_dgi_status", "Error", update_modified=False)
    doc.db_set("custom_dgi_error", (msg or "")[:140], update_modified=False)


# ---------------- Scheduled token monitoring ----------------

def _should_run_today(settings) -> bool:
    freq = (settings.check_frequency or "Daily")
    today = getdate(nowdate())
    if freq == "Weekly":
        return WEEKDAYS[today.weekday()] == (settings.check_weekday or "Monday")
    if freq == "Monthly":
        return today.day == int(settings.check_day_of_month or 1)
    return True  # Daily (default)


def check_token_expiry():
    """Daily scheduler entry. Runs only on the cadence chosen in Settings, then alerts
    if the e-DEF JWT is missing/expired or within `warn_days_before` of expiry."""
    settings = get_settings()
    if not settings.enabled or not _should_run_today(settings):
        return

    res = client.info_status()
    data = res.get("data") or {}
    _log("token-check", None, data, res.get("error"))

    if not res.get("ok"):
        _notify(settings, "ALERTE jeton e-DEF: appel /api/info/status en echec",
                f"Statut HTTP={res.get('status')}, erreur={res.get('error')}. "
                "Le jeton est peut-etre expire/invalide ou l'hote injoignable.")
        return

    token_valid = data.get("tokenValid")
    if token_valid:
        try:
            frappe.db.set_value("DGI Compliance Settings", None, "token_valid_until", token_valid)
        except Exception:
            pass
        days_left = date_diff(getdate(get_datetime(token_valid)), getdate(nowdate()))
        warn = int(settings.warn_days_before or 7)
        if days_left <= warn:
            _notify(settings, f"Jeton e-DEF expire dans {days_left} jour(s)",
                    f"Le jeton e-DEF (env. {settings.environment}) expire le {token_valid} "
                    f"({days_left} jour(s)). Regenerez-le sur le portail e-MCF et mettez a jour "
                    "DGI Compliance Settings -> e-DEF JWT Token.")
    if data.get("status") is False:
        _notify(settings, "API e-DEF indisponible", "GET /api/info/status renvoie status=false.")


def _notify(settings, subject, message):
    recipients = [e.strip() for e in (settings.notify_recipients or "").replace(";", ",").split(",") if e.strip()]
    if not recipients:
        # Fallback: every System Manager with a valid email
        recipients = frappe.get_all(
            "Has Role", filters={"role": "System Manager", "parenttype": "User"}, pluck="parent")
        recipients = [r for r in recipients if r and "@" in r]
    try:
        if recipients:
            frappe.sendmail(recipients=recipients, subject="[DGI] " + subject, message=message)
    except Exception:
        pass
    frappe.log_error(title="[DGI] " + subject, message=message)
