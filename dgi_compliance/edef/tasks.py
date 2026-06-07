"""Document events, normalization flow, retry, currency reconciliation, token monitoring."""
import frappe
from frappe import _
from frappe.utils import nowdate, getdate, get_datetime, date_diff, flt
from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings
from dgi_compliance.edef import client
from dgi_compliance.edef.mapper import build_invoice_request, validate_invoice_request
from dgi_compliance.edef.rounding import doc_vat_base
from dgi_compliance.edef.qr import make_qr_data_uri
from dgi_compliance.edef.audit import log_exchange

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ---------------- Sales Invoice events ----------------

def on_sales_invoice_submit(doc, method=None):
    settings = get_settings()
    if not settings.enabled:
        return
    normalize_invoice(doc, settings)


def on_sales_invoice_cancel(doc, method=None):
    settings = get_settings()
    if not settings.enabled:
        return
    uid = doc.get("custom_dgi_uid")
    if not uid:
        return
    res = client.cancel_invoice(uid)
    log_exchange("cancel", {"uid": uid}, res.get("data"), res.get("error"), reference_invoice=doc.name)
    doc.db_set("custom_dgi_status", "Cancelled", update_modified=False)


@frappe.whitelist()
def retry_normalization(invoice: str) -> dict:
    """Re-run the e-DEF normalization for a submitted invoice (used by the 'Retry' button)."""
    frappe.only_for(["System Manager", "Accounts Manager"])
    doc = frappe.get_doc("Sales Invoice", invoice)
    if doc.docstatus != 1:
        frappe.throw(_("La facture doit etre soumise."))
    settings = get_settings()
    if not settings.enabled:
        frappe.throw(_("DGI Compliance est desactive."))
    log_exchange("retry", {"invoice": invoice}, None, reference_invoice=invoice)
    return normalize_invoice(doc, settings) or {"ok": True}


# ---------------- Core normalization ----------------

def normalize_invoice(doc, settings=None):
    settings = settings or get_settings()

    dto = build_invoice_request(doc)
    errors = validate_invoice_request(dto)
    if errors:
        _mark_error(doc, "; ".join(errors))
        log_exchange("validate", dto, None, errors, reference_invoice=doc.name)
        frappe.msgprint(_("Normalisation DGI: payload invalide - ") + "; ".join(errors))
        return {"ok": False, "stage": "validate", "errors": errors}

    created = client.create_invoice(dto)
    log_exchange("create", dto, created.get("data"), created.get("error"),
                 status_code=created.get("status"), url=created.get("url"), reference_invoice=doc.name)
    data = created.get("data") or {}
    if not created.get("ok") or not data.get("uid") or data.get("errorCode"):
        _mark_error(doc, data.get("errorDesc") or created.get("error") or "create failed")
        return {"ok": False, "stage": "create"}

    uid = data["uid"]
    dgi_total = flt(data.get("total"))      # CDF
    dgi_vtotal = flt(data.get("vtotal"))    # CDF
    doc.db_set("custom_dgi_uid", uid, update_modified=False)

    # --- Currency / total reconciliation in CDF (DGI recalculates everything) ---
    erp_total_cdf = flt(doc.base_grand_total)
    total_diff = dgi_total - erp_total_cdf   # >0 => DGI higher than ERPNext
    tol = flt(settings.dgi_total_tolerance or 500)
    if abs(total_diff) > tol:
        msg = (f"Ecart total > tolerance: ERPNext={erp_total_cdf} CDF vs DGI={dgi_total} CDF "
               f"(diff={round(total_diff, 2)}, tolerance={tol}).")
        log_exchange("reconcile", {"erp_total": erp_total_cdf, "dgi_total": dgi_total, "diff": total_diff},
                     None, msg, reference_invoice=doc.name)
        if (settings.reconcile_action or "Alert only") == "Block":
            cancelled = client.cancel_invoice(uid)
            log_exchange("cancel", {"uid": uid, "reason": "total-mismatch"}, cancelled.get("data"),
                         cancelled.get("error"), reference_invoice=doc.name)
            _mark_error(doc, msg)
            frappe.throw(_("Normalisation DGI bloquee - ") + msg)
            return {"ok": False, "stage": "reconcile-total"}
        frappe.msgprint(_("Avertissement DGI - ") + msg, indicator="orange")

    # --- Optional VAT-only reconciliation (alert) ---
    if settings.reconcile_with_emcf and (settings.vat_accounts or []):
        erp_vat = doc_vat_base(doc, settings)
        vat_diff = abs(flt(erp_vat) - dgi_vtotal)
        if vat_diff > flt(settings.reconcile_tolerance or 0):
            vmsg = f"Ecart TVA: ERPNext={erp_vat} vs e-MCF={dgi_vtotal} (diff={round(vat_diff, 4)})."
            log_exchange("reconcile", {"erp_vat": erp_vat, "emcf_vtotal": dgi_vtotal}, None, vmsg,
                         reference_invoice=doc.name)
            frappe.msgprint(_("Avertissement TVA DGI - ") + vmsg, indicator="orange")

    if not settings.auto_normalize:
        doc.db_set("custom_dgi_status", "Pending", update_modified=False)
        return {"ok": True, "stage": "pending", "uid": uid}

    confirmed = client.confirm_invoice(uid, dgi_total, dgi_vtotal)
    log_exchange("confirm", {"uid": uid, "total": dgi_total, "vtotal": dgi_vtotal}, confirmed.get("data"),
                 confirmed.get("error"), status_code=confirmed.get("status"), reference_invoice=doc.name)
    cdata = confirmed.get("data") or {}
    if not confirmed.get("ok") or cdata.get("errorCode") or not cdata.get("codeDEFDGI"):
        _mark_error(doc, cdata.get("errorDesc") or confirmed.get("error") or "confirm failed")
        return {"ok": False, "stage": "confirm"}

    doc.db_set({
        "custom_dgi_status": "Normalized",
        "custom_dgi_code_def": cdata.get("codeDEFDGI"),
        "custom_dgi_counters": cdata.get("counters"),
        "custom_dgi_nim": cdata.get("nim"),
        "custom_dgi_datetime": cdata.get("dateTime"),
        "custom_dgi_qr_code": cdata.get("qrCode"),
        "custom_dgi_qr_image": make_qr_data_uri(cdata.get("qrCode")),
        "custom_dgi_error": None,
    }, update_modified=False)

    # --- Post the small DGI exchange difference to the configurable account ---
    if settings.auto_post_exchange_diff and total_diff and abs(total_diff) <= tol:
        je = _post_exchange_difference(doc, settings, erp_total_cdf, dgi_total, total_diff)
        if je:
            doc.db_set("custom_dgi_exchange_je", je, update_modified=False)

    return {"ok": True, "stage": "normalized", "uid": uid, "code_def": cdata.get("codeDEFDGI")}


def _post_exchange_difference(doc, settings, erp_total, dgi_total, diff):
    """Create a Journal Entry booking the (small) ERPNext-vs-DGI total difference to the
    configurable exchange-difference account. Counter account must be a non-party GL account."""
    ex_acc = settings.exchange_difference_account
    ctr_acc = settings.exchange_diff_counter_account
    if not ex_acc or not ctr_acc:
        log_exchange("exchange-diff", {"diff": diff}, None,
                     "Comptes d'ecart de change non configures", reference_invoice=doc.name)
        return None
    amount = abs(flt(diff))
    if amount == 0:
        return None
    try:
        je = frappe.new_doc("Journal Entry")
        je.voucher_type = "Journal Entry"
        je.company = doc.company
        je.posting_date = doc.posting_date
        je.user_remark = (f"Ecart DGI facture {doc.name}: ERPNext={erp_total} CDF vs DGI={dgi_total} CDF "
                          f"(diff={round(diff, 2)} CDF)")
        if diff > 0:  # DGI higher than ERPNext
            je.append("accounts", {"account": ctr_acc, "debit_in_account_currency": amount})
            je.append("accounts", {"account": ex_acc, "credit_in_account_currency": amount})
        else:
            je.append("accounts", {"account": ex_acc, "debit_in_account_currency": amount})
            je.append("accounts", {"account": ctr_acc, "credit_in_account_currency": amount})
        je.insert(ignore_permissions=True)
        je.submit()
        log_exchange("exchange-diff", {"diff": diff, "je": je.name}, None, reference_invoice=doc.name)
        return je.name
    except Exception as e:
        log_exchange("exchange-diff", {"diff": diff}, None, str(e), reference_invoice=doc.name)
        frappe.msgprint(_("Ecart de change non comptabilise: ") + str(e), indicator="orange")
        return None


def _mark_error(doc, msg):
    doc.db_set("custom_dgi_status", "Error", update_modified=False)
    doc.db_set("custom_dgi_error", (msg or "")[:140], update_modified=False)


# ---------------- Scheduled token monitoring ----------------

def _should_run_today(settings):
    freq = (settings.check_frequency or "Daily")
    today = getdate(nowdate())
    if freq == "Weekly":
        return WEEKDAYS[today.weekday()] == (settings.check_weekday or "Monday")
    if freq == "Monthly":
        return today.day == int(settings.check_day_of_month or 1)
    return True


def check_token_expiry():
    settings = get_settings()
    if not settings.enabled or not _should_run_today(settings):
        return
    res = client.info_status()
    data = res.get("data") or {}
    log_exchange("token-check", None, data, res.get("error"), status_code=res.get("status"))
    if not res.get("ok"):
        _notify(settings, "ALERTE jeton e-DEF: /api/info/status en echec",
                f"Statut HTTP={res.get('status')}, erreur={res.get('error')}.")
        return
    token_valid = data.get("tokenValid")
    if token_valid:
        try:
            frappe.db.set_value("DGI Compliance Settings", None, "token_valid_until", token_valid)
        except Exception:
            pass
        days_left = date_diff(getdate(get_datetime(token_valid)), getdate(nowdate()))
        if days_left <= int(settings.warn_days_before or 7):
            _notify(settings, f"Jeton e-DEF expire dans {days_left} jour(s)",
                    f"Le jeton e-DEF (env. {settings.environment}) expire le {token_valid}. "
                    "Regenerez-le sur le portail e-MCF et mettez a jour DGI Compliance Settings.")
    if data.get("status") is False:
        _notify(settings, "API e-DEF indisponible", "GET /api/info/status renvoie status=false.")


def _notify(settings, subject, message):
    recipients = [e.strip() for e in (settings.notify_recipients or "").replace(";", ",").split(",") if e.strip()]
    if not recipients:
        recipients = frappe.get_all("Has Role", filters={"role": "System Manager", "parenttype": "User"}, pluck="parent")
        recipients = [r for r in recipients if r and "@" in r]
    try:
        if recipients:
            frappe.sendmail(recipients=recipients, subject="[DGI] " + subject, message=message)
    except Exception:
        pass
    frappe.log_error(title="[DGI] " + subject, message=message)
