"""Document events, normalization flow, retry, currency reconciliation, token monitoring.

v3.0 posting gate
-----------------
A Sales Invoice can only be POSTED (submitted -> GL / stock impact) once it is NORMALIZED.
Non-normalized invoices stay DRAFTS (no GL, no stock, no accounting impact) and are listed in
the dedicated report "Factures en attente de normalisation". The gate lives in `before_submit`:

  * normalization_gating = Enforce (default):
      - status == Normalized          -> submit allowed
      - auto_normalize on             -> normalize now; allow if it succeeds, else block
      - auto_normalize off            -> block; user must click "Normaliser (DGI)" on the draft
      - authorised override + flag    -> allowed despite failure
  * normalization_gating = Off:
      - classic behaviour: invoice submits, then normalizes in `on_submit`.
"""
import frappe
from frappe import _
from frappe.utils import nowdate, getdate, get_datetime, date_diff, flt, now_datetime
from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings
from dgi_compliance.edef import client
from dgi_compliance.edef.mapper import build_invoice_request, validate_invoice_request, TaxGroupMappingError, COMMENT_LETTERS
from dgi_compliance.edef.rounding import doc_vat_base
from dgi_compliance.edef.qr import make_qr_data_uri
from dgi_compliance.edef.audit import log_exchange
from dgi_compliance.edef.util import to_db_datetime

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Result fields cleared when a normalized draft is edited and must be re-normalized.
_RESULT_FIELDS = ("custom_dgi_uid", "custom_dgi_code_def", "custom_dgi_counters", "custom_dgi_nim",
                  "custom_dgi_datetime", "custom_dgi_qr_code", "custom_dgi_qr_image", "custom_dgi_error")


def _can_override(settings):
    from dgi_compliance.edef.matrix import _can_override as _co
    return _co(settings)


def enforce_return_invoice_type(doc, method=None):
    """`validate` hook (runs FIRST). Returns: force FA/EA. Non-returns (v3.6): default the
    type so it is never empty - Sales Order value if any, else FV (EV for export). FT/ET stay
    an explicit choice."""
    settings = get_settings()
    if not settings.enabled:
        return
    if doc.get("is_return"):
        want = "EA" if doc.get("custom_dgi_export") else "FA"
        if (doc.get("dgi_invoice_type") or "") != want:
            doc.dgi_invoice_type = want
        return
    # v3.6: a non-return invoice never keeps an empty DGI type. Precedence:
    # explicit value > type carried by the linked Sales Order > FV (EV for export).
    # FT/ET remain an explicit user choice (acompte) and are preserved when selected.
    if doc.get("dgi_invoice_type"):
        return
    so_type = _invoice_type_from_sales_order(doc)
    if so_type in ("FV", "FT", "EV", "ET"):
        doc.dgi_invoice_type = so_type
    else:
        doc.dgi_invoice_type = "EV" if doc.get("custom_dgi_export") else "FV"


def _invoice_type_from_sales_order(doc):
    """First DGI invoice type found on the Sales Orders linked to the invoice lines."""
    try:
        seen = set()
        for it in (doc.get("items") or []):
            so = it.get("sales_order") if hasattr(it, "get") else getattr(it, "sales_order", None)
            if not so or so in seen:
                continue
            seen.add(so)
            t = frappe.db.get_value("Sales Order", so, "dgi_invoice_type")
            if t:
                return t
    except Exception:
        pass
    return None


# ---------------- Sales Invoice draft status management ----------------

def manage_draft_normalization_status(doc, method=None):
    """`validate` hook (runs last): keep the draft's DGI status coherent.

    * a brand-new / not-yet-normalized draft is marked 'Pending' so it shows up in the
      "Factures en attente de normalisation" list;
    * a draft that was already Normalized but has since changed (amounts / type) is reset to
      'Pending' and its e-DEF result is cleared, forcing a fresh normalization before posting.
    """
    settings = get_settings()
    if not settings.enabled:
        return
    if doc.docstatus != 0:
        return

    status = doc.get("custom_dgi_status")
    if status == "Normalized" and not doc.is_new():
        # v3.5: watch every field that changes the e-DEF payload, not just the totals.
        watched = ("base_grand_total", "grand_total", "dgi_invoice_type", "custom_dgi_export",
                   "customer", "currency", "conversion_rate", "taxes_and_charges",
                   "posting_date", "custom_dgi_reference", "custom_dgi_reference_type")
        changed = (any(doc.has_value_changed(f) for f in watched)
                   or any(doc.has_value_changed("dgi_comment_" + l) for l in COMMENT_LETTERS))
        if changed:
            doc.custom_dgi_status = "Pending"
            for f in _RESULT_FIELDS:
                doc.set(f, None)
            frappe.msgprint(
                _("Facture modifiee apres normalisation: statut remis a 'Pending'. "
                  "Re-normalisez avant de soumettre."), indicator="orange", title=_("DGI"))
            return

    if not status:
        doc.custom_dgi_status = "Pending"


# ---------------- Sales Invoice events ----------------

def register_print(doc, method=None, *args, **kwargs):
    """`before_print` hook: increment the print counter and stamp timestamps.
    ORIGINAL on the first print, DUPLICATA afterwards. Never blocks printing (best-effort)."""
    try:
        settings = get_settings()
        if not settings.enabled or doc.docstatus != 1:
            return
        count = int(doc.get("custom_dgi_print_count") or 0) + 1
        now = now_datetime()
        updates = {"custom_dgi_print_count": count, "custom_dgi_last_printed_on": now}
        if count == 1:
            updates["custom_dgi_first_printed_on"] = now
        for k, v in updates.items():
            doc.set(k, v)  # reflect on the in-memory doc so the current render shows the label
        doc.db_set(updates, update_modified=False)
    except Exception:
        pass


def before_sales_invoice_submit(doc, method=None):
    """Posting gate: block submit unless the invoice is normalized (see module docstring)."""
    settings = get_settings()
    if not settings.enabled:
        return
    gating = (getattr(settings, "normalization_gating", None) or "Enforce")
    if gating == "Off":
        return  # classic behaviour; on_submit will normalize after posting

    if doc.get("custom_dgi_status") == "Normalized":
        return

    overridden = bool(doc.get("dgi_validation_override")) and _can_override(settings)

    if settings.auto_normalize:
        res = normalize_invoice(doc, settings)
        if doc.get("custom_dgi_status") == "Normalized":
            return
        if overridden:
            frappe.msgprint(_("Postage force malgre l'echec de normalisation (override)."),
                            indicator="orange", title=_("DGI"))
            return
        stage = (res or {}).get("stage", "?")
        frappe.throw(
            _("Postage bloque: la facture n'a pas pu etre normalisee (etape: {0}). "
              "Corrigez l'erreur DGI (champ 'Erreur DGI') puis re-essayez, ou cochez "
              "'Override validation DGI'.").format(stage),
            title=_("Normalisation DGI requise"))
    else:
        if overridden:
            frappe.msgprint(_("Postage force sans normalisation (override)."),
                            indicator="orange", title=_("DGI"))
            return
        frappe.throw(
            _("Cette facture n'est pas encore normalisee. Ouvrez le brouillon, cliquez "
              "'Normaliser (DGI)', puis soumettez. (Ou activez 'Auto-normalize on Submit' "
              "dans DGI Compliance Settings.)"),
            title=_("Normalisation DGI requise"))


def on_sales_invoice_submit(doc, method=None):
    settings = get_settings()
    if not settings.enabled:
        return
    gating = (getattr(settings, "normalization_gating", None) or "Enforce")
    if gating == "Off":
        # Classic behaviour: normalize right after posting.
        normalize_invoice(doc, settings)
    # Enforce mode: already normalized in before_submit; nothing to do here.


def on_sales_invoice_cancel(doc, method=None):
    settings = get_settings()
    if not settings.enabled:
        return
    # v4.0: MCF physique - rien ne part vers la DGI; on annule les jobs locaux en attente.
    if settings.is_mcf_mode():
        from dgi_compliance.mcf.jobs import cancel_pending_jobs
        cancel_pending_jobs(doc.name)
        if doc.get("custom_dgi_status"):
            doc.db_set("custom_dgi_status", "Cancelled", update_modified=False)
        frappe.msgprint(_("Mode MCF Physique: aucune annulation n'est transmise a la DGI. Si la "
                          "facture etait deja normalisee par le MCF, emettez un avoir (FA/EA) "
                          "reference sur son Code DEF."), indicator="orange", title=_("DGI"))
        return
    uid = doc.get("custom_dgi_uid")
    if not uid:
        return
    res = client.cancel_invoice(uid)
    log_exchange("cancel", {"uid": uid}, res.get("data"), res.get("error"),
                 status_code=res.get("status"), reference_invoice=doc.name)
    doc.db_set("custom_dgi_status", "Cancelled", update_modified=False)
    # v3.5: an already-CONFIRMED invoice usually cannot be cancelled at the DGI; the compliant
    # correction is a credit note (FA/EA). Warn the user instead of failing silently.
    if not res.get("ok"):
        frappe.msgprint(
            _("Annulation cote DGI en echec (HTTP {0}). Si la facture etait deja confirmee/"
              "normalisee, emettez un avoir (FA/EA) reference sur son Code DEF pour la "
              "corriger fiscalement.").format(res.get("status")),
            indicator="orange", title=_("DGI"))


# ---------------- Whitelisted actions (buttons) ----------------

@frappe.whitelist()
def normalize_sales_invoice(invoice: str) -> dict:
    """Normalize a DRAFT (or submitted) Sales Invoice on demand ('Normaliser (DGI)' button).
    On a draft, success sets status = Normalized and unlocks posting."""
    frappe.only_for(["System Manager", "Accounts Manager"])
    doc = frappe.get_doc("Sales Invoice", invoice)
    if not frappe.has_permission("Sales Invoice", "write", doc=doc):
        frappe.throw(_("Permission insuffisante sur cette facture."), frappe.PermissionError)
    if doc.docstatus == 2:
        frappe.throw(_("Facture annulee: normalisation impossible."))
    settings = get_settings()
    if not settings.enabled:
        frappe.throw(_("DGI Compliance est desactive."))
    log_exchange("normalize-manual", {"invoice": invoice}, None, reference_invoice=invoice)
    return normalize_invoice(doc, settings) or {"ok": True}


@frappe.whitelist()
def retry_normalization(invoice: str) -> dict:
    """Re-run the e-DEF normalization for a SUBMITTED invoice (the 'Retry' button)."""
    frappe.only_for(["System Manager", "Accounts Manager"])
    doc = frappe.get_doc("Sales Invoice", invoice)
    if not frappe.has_permission("Sales Invoice", "write", doc=doc):
        frappe.throw(_("Permission insuffisante sur cette facture."), frappe.PermissionError)
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

    # v3.5 concurrency guard: another user/session may have normalized this invoice while the
    # form was open. Re-read the DB status and never create a second DGI invoice.
    if not doc.is_new():
        db_status = frappe.db.get_value("Sales Invoice", doc.name, "custom_dgi_status")
        if db_status == "Normalized" and doc.get("custom_dgi_status") == "Normalized":
            return {"ok": True, "stage": "normalized", "uid": doc.get("custom_dgi_uid"),
                    "code_def": doc.get("custom_dgi_code_def")}

    # v4.0: physical MCF mode - normalization is performed by the local USB agent.
    # No e-DEF API call may leave the server while this mode is active (see client.py guard).
    if settings.is_mcf_mode():
        from dgi_compliance.mcf.jobs import queue_mcf_job
        return queue_mcf_job(doc, settings)

    try:
        dto = build_invoice_request(doc)
    except TaxGroupMappingError as e:
        msg = str(e)
        _mark_error(doc, msg)
        log_exchange("validate", {"invoice": doc.name}, None, msg, reference_invoice=doc.name)
        raise
    errors = validate_invoice_request(dto)
    if errors:
        _mark_error(doc, "; ".join(errors))
        log_exchange("validate", dto, None, errors, reference_invoice=doc.name)
        frappe.msgprint(_("Normalisation DGI: payload invalide - ") + "; ".join(errors))
        return {"ok": False, "stage": "validate", "errors": errors}

    # v3.5 payment coherence (warning only): for POS invoices the sum of payments should match
    # the invoice total; the DGI reconciles them on its side.
    _warn_payment_mismatch(doc, dto)

    # v3.5 stale-UID cleanup: a previous attempt may have CREATED an invoice at the DGI without
    # confirming it (confirm failed / network cut). Cancel it (best-effort) before re-creating,
    # so no orphan invoices accumulate on the DGI side.
    stale_uid = doc.get("custom_dgi_uid")
    if stale_uid and doc.get("custom_dgi_status") != "Normalized":
        res = client.cancel_invoice(stale_uid)
        log_exchange("cancel-stale", {"uid": stale_uid}, res.get("data"), res.get("error"),
                     status_code=res.get("status"), reference_invoice=doc.name)
        doc.db_set("custom_dgi_uid", None, update_modified=False)

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
    # abs(): credit notes carry a negative grand total in ERPNext, but the DGI works in positive.
    erp_total_cdf = abs(flt(doc.base_grand_total))
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
        erp_vat = abs(doc_vat_base(doc, settings))
        vat_diff = abs(flt(erp_vat) - dgi_vtotal)
        if vat_diff > flt(settings.reconcile_tolerance or 0):
            vmsg = f"Ecart TVA: ERPNext={erp_vat} vs e-MCF={dgi_vtotal} (diff={round(vat_diff, 4)})."
            log_exchange("reconcile", {"erp_vat": erp_vat, "emcf_vtotal": dgi_vtotal}, None, vmsg,
                         reference_invoice=doc.name)
            frappe.msgprint(_("Avertissement TVA DGI - ") + vmsg, indicator="orange")

    # v3.5 FIX: the confirm step ALWAYS runs. Before v3.5, auto_normalize=off left manual
    # normalizations at the 'create' stage (uid but no confirm) so the invoice could never
    # reach 'Normalized' and posting stayed blocked. auto_normalize only decides WHEN
    # normalization is triggered (automatically at submit), never whether confirm happens.
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


def _warn_payment_mismatch(doc, dto):
    """Warn (never block) when POS payment lines don't add up to the invoice total in CDF.
    The DGI cross-checks payments against the total; a mismatch surfaces as an API error."""
    payments = dto.get("payment") or []
    if not payments:
        return
    try:
        paid = sum(flt(p.get("amount")) for p in payments)
        total = abs(flt(doc.get("base_grand_total")))
        if total and abs(paid - total) > max(flt(doc.get("base_rounding_adjustment") or 0), 1):
            frappe.msgprint(
                _("Paiements ({0} CDF) differents du total facture ({1} CDF). La DGI peut "
                  "rejeter la facture; verifiez les lignes de paiement.").format(
                    round(paid, 2), round(total, 2)),
                indicator="orange", title=_("DGI"))
    except Exception:
        pass


def _mark_error(doc, msg):
    doc.db_set("custom_dgi_status", "Error", update_modified=False)
    doc.db_set("custom_dgi_error", (msg or "")[:140], update_modified=False)
    # The 140-char field is a summary; keep the FULL message in the audit trail.
    if msg and len(str(msg)) > 140:
        log_exchange("error-detail", {"invoice": doc.name}, None, str(msg),
                     reference_invoice=doc.name)


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
    if get_settings().is_mcf_mode():
        return  # v4.0: MCF physique actif - le token e-DEF est hors sujet
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
            frappe.db.set_value("DGI Compliance Settings", None, "token_valid_until", to_db_datetime(token_valid))
        except Exception:
            pass
        days_left = date_diff(getdate(to_db_datetime(token_valid) or nowdate()), getdate(nowdate()))
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
