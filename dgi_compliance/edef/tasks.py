"""Document events, e-MCF normalization flow, retry and token monitoring.

v5.0.0 (HL1) - fiscal integrity
-------------------------------
* Posting gate WITHOUT exception: a Sales Invoice is submitted only once normalized
  (SFE 1.1.5; dossier d'homologation, engagement k). The "Override validation DGI" box now
  only relaxes the validation matrices, never the normalization.
* The normalization runs in its own committed transaction: the UID, the security elements and
  the exchange journal survive a later failure of the submit (SFE 1.1.20).
* Before any POST the pending requests of the e-MCF are checked, so a lost response never
  creates a second DGI invoice (API e-MCF v1.3: max 10 pending, error 63).
* The amounts returned by the e-MCF are recomputed by the SFE before CONFIRM; any difference
  cancels the request (API e-MCF v1.3, workflow p.5).
* A normalized invoice can neither be modified, deleted nor cancelled in ERPNext: the fiscal
  correction is a credit note FA/EA (nature RAN, RAM, COR or RRR).
"""
import hashlib
import json
import re

import frappe
from frappe import _
from frappe.utils import nowdate, getdate, date_diff, flt, now_datetime

from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings
from dgi_compliance.edef import client
from dgi_compliance.edef.mapper import (
    build_invoice_request, validate_invoice_request, TaxGroupMappingError, COMMENT_LETTERS,
)
from dgi_compliance.edef.payments import PaymentTypeMappingError
from dgi_compliance.edef.calc import compute_totals, compare, response_groups, response_rates
from dgi_compliance.edef.errors import describe, is_retryable
from dgi_compliance.edef.rounding import doc_vat_base
from dgi_compliance.edef.qr import make_qr_data_uri
from dgi_compliance.edef.audit import log_exchange
from dgi_compliance.edef.util import to_db_datetime

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
UNCERTAIN_PREFIX = "CONFIRMATION INCERTAINE"

# Security elements and fiscal trace: written only by the normalization flow (db_set).
FISCAL_FIELDS = (
    "custom_dgi_status", "custom_dgi_uid", "custom_dgi_code_def", "custom_dgi_counters",
    "custom_dgi_nim", "custom_dgi_datetime", "custom_dgi_qr_code", "custom_dgi_qr_image",
    "custom_dgi_response", "custom_dgi_normalized_on", "custom_dgi_operator",
    "custom_dgi_operator_name", "custom_dgi_payload_hash", "dgi_invoice_type",
    "dgi_point_of_sale", "custom_dgi_channel", "custom_dgi_operation",
)
_RESULT_FIELDS = ("custom_dgi_uid", "custom_dgi_code_def", "custom_dgi_counters", "custom_dgi_nim",
                  "custom_dgi_datetime", "custom_dgi_qr_code", "custom_dgi_qr_image",
                  "dgi_sequence_ref")     # v5.4.0: reference sequentielle (fiscal.sequence), written by the SFE


def _commit():
    """Persist the fiscal state immediately (independent of the surrounding submit)."""
    if not frappe.flags.get("dgi_no_commit"):
        frappe.db.commit()


def _set(doc, values):
    doc.db_set(values, update_modified=False)


def _is_normalized(doc):
    return doc.get("custom_dgi_status") == "Normalized" or bool(doc.get("custom_dgi_code_def"))


def payload_hash(dto):
    """Fingerprint of the fiscal content (operator excluded: it is who clicks, not what is sold)."""
    data = {k: v for k, v in (dto or {}).items() if k != "operator"}
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:32]


# ---------------- validate hooks ----------------

def enforce_return_invoice_type(doc, method=None):
    """`validate` hook (runs FIRST). Returns: force FA/EA. Non-returns: never empty -
    Sales Order value if any, else FV (EV for export). FT/ET stay an explicit choice."""
    settings = get_settings()
    if not settings.enabled:
        return
    if doc.get("is_return"):
        want = "EA" if doc.get("custom_dgi_export") else "FA"
        if (doc.get("dgi_invoice_type") or "") != want:
            doc.dgi_invoice_type = want
        return
    if doc.get("dgi_invoice_type"):
        return
    so_type = _invoice_type_from_sales_order(doc)
    if so_type in ("FV", "FT", "EV", "ET"):
        doc.dgi_invoice_type = so_type
    else:
        doc.dgi_invoice_type = "EV" if doc.get("custom_dgi_export") else "FV"


def _invoice_type_from_sales_order(doc):
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


def manage_draft_normalization_status(doc, method=None):
    """`validate` hook (runs last).

    * a new / not-yet-normalized draft is marked 'Pending';
    * a NORMALIZED draft is fiscally final: any change of its fiscal content is refused
      (the e-MCF already holds it; correction = credit note after submission)."""
    settings = get_settings()
    if not settings.enabled or doc.docstatus != 0:
        return
    status = doc.get("custom_dgi_status")
    if status == "Normalized" and not doc.is_new():
        changed = False
        stored = doc.get("custom_dgi_payload_hash")
        if stored:
            try:
                changed = payload_hash(build_invoice_request(doc)) != stored
            except Exception:
                changed = True
        else:  # invoice normalized by v4 (no fingerprint): watch the main fields
            watched = ("base_grand_total", "grand_total", "dgi_invoice_type", "custom_dgi_export",
                       "customer", "currency", "conversion_rate", "taxes_and_charges",
                       "custom_dgi_reference", "custom_dgi_reference_type")
            changed = (any(doc.has_value_changed(f) for f in watched)
                       or any(doc.has_value_changed("dgi_comment_" + c) for c in COMMENT_LETTERS))
        if changed:
            frappe.throw(
                _("Cette facture est deja normalisee aupres de la DGI (Code DEF/DGI {0}). Son contenu "
                  "fiscal (articles, montants, client, paiement, type) ne peut plus etre modifie. "
                  "Soumettez-la, puis emettez une facture d'avoir pour la corriger.")
                .format(doc.get("custom_dgi_code_def") or "-"),
                title=_("Facture normalisee"))
        return
    if not status:
        doc.custom_dgi_status = "Pending"
    if not doc.is_new():
        _guard_mcf_operation(doc)


def _guard_mcf_operation(doc):
    """HL2 - MCF channel. A draft whose operation is still QUEUED may be changed: the operation
    is cancelled (it never reached the device). Once leased to a gateway, the fiscal content is
    frozen like a normalized invoice (the MCF may already hold it)."""
    if not frappe.db.table_exists("DGI Fiscal Operation"):
        return
    from dgi_compliance.fiscal.operations import active_operation, cancel_queued_for_invoice
    op = active_operation(doc.name)
    if not op:
        return
    try:
        changed = payload_hash(build_invoice_request(doc)) != op.payload_hash
    except Exception:
        changed = True
    if not changed or (op.get("channel") == "e-MCF" and op.status == "Pending"):
        return
    if op.status == "Queued":
        cancel_queued_for_invoice(doc.name, _("Facture modifiee avant traitement par le MCF"))
        doc.custom_dgi_status = "Pending"
        doc.custom_dgi_operation = None
        return
    frappe.throw(_("Cette facture est en cours de normalisation sur le canal {2} (operation {0}, statut {1}). "
                   "Son contenu fiscal ne peut plus etre modifie.").format(op.name, op.status, op.get("channel") or "MCF"),
                 title=_("Normalisation DGI en cours"))


# ---------------- document events ----------------

def register_print(doc, method=None, *args, **kwargs):
    """`before_print`: ORIGINAL on the first print, DUPLICATA afterwards (best effort)."""
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
            doc.set(k, v)
        doc.db_set(updates, update_modified=False)
    except Exception:
        pass


def before_sales_invoice_submit(doc, method=None):
    """Posting gate: no submission without the security elements of the e-MCF."""
    settings = get_settings()
    if not settings.enabled:
        return
    if doc.get("custom_dgi_status") == "Normalized" and doc.get("custom_dgi_code_def"):
        return
    channel = _channel_of(doc, settings)
    if channel == "MCF":
        _submit_gate_mcf(doc, settings)
        return
    if settings.auto_normalize and not frappe.db.exists("Sales Invoice", doc.name):
        # Brand-new invoice inserted and submitted in one call (POS): there is no draft to keep
        # the fiscal state, so the normalization runs in on_submit, AFTER the accounting entries,
        # inside the same transaction: any failure rolls everything back (no orphan at the DGI).
        doc.flags.dgi_normalize_on_submit = True
        return
    if not settings.auto_normalize:
        frappe.throw(
            _("Cette facture n'est pas encore normalisee. Cliquez sur DGI > 'Normaliser (DGI)', "
              "puis soumettez-la (ou activez 'Auto-normalize on Submit' dans DGI Compliance Settings)."),
            title=_("Normalisation DGI requise"))
    res = normalize_invoice(doc, settings) or {}
    if doc.get("custom_dgi_status") == "Normalized" or \
            frappe.db.get_value("Sales Invoice", doc.name, "custom_dgi_status") == "Normalized":
        for f in _RESULT_FIELDS + ("custom_dgi_status", "custom_dgi_channel", "custom_dgi_operation"):
            doc.set(f, frappe.db.get_value("Sales Invoice", doc.name, f))
        return
    if res.get("stage") == "queued-mcf":
        frappe.throw(_("e-MCF indisponible : facture transmise au MCF de secours (operation {0}, position {1}). "
                       "Elle sera validable des reception des elements de securite.")
                     .format(res.get("operation"), res.get("position") or 1), title=_("Secours MCF"))
    frappe.throw(
        _("Soumission bloquee: la facture n'a pas pu etre normalisee par la DGI.<br><b>{0}</b><br>"
          "Corrigez la cause puis soumettez a nouveau. Aucune facture ne peut etre enregistree sans "
          "les elements de securite de l'e-MCF.").format(res.get("message") or doc.get("custom_dgi_error") or res.get("stage", "?")),
        title=_("Normalisation DGI requise"))


def _channel_of(doc, settings):
    from dgi_compliance.fiscal.router import RoutingError, route
    try:
        return route(doc, settings).channel
    except RoutingError as e:
        if doc.name and frappe.db.exists("Sales Invoice", doc.name):
            _mark_error(doc, str(e))       # kept on the draft: the submit itself is rolled back
            _commit()
        frappe.throw(str(e), title=_("Point de vente fiscal"))


def _submit_gate_mcf(doc, settings):
    """MCF channel (HL2): the security elements come back asynchronously from the site gateway.
    The invoice must exist as a draft so that its operation survives the end of this request."""
    if not frappe.db.exists("Sales Invoice", doc.name):
        frappe.throw(_("Ce point de vente normalise sur le MCF : enregistrez d'abord la facture (brouillon), "
                       "elle sera validable des reception des elements de securite du MCF."),
                     title=_("Normalisation MCF"))
    if not settings.auto_normalize:
        frappe.throw(_("Cette facture n'est pas encore normalisee. Cliquez sur DGI > 'Normaliser (DGI)', "
                       "puis soumettez-la."), title=_("Normalisation DGI requise"))
    res = normalize_invoice(doc, settings) or {}
    if frappe.db.get_value("Sales Invoice", doc.name, "custom_dgi_status") == "Normalized":
        for f in _RESULT_FIELDS + ("custom_dgi_status", "custom_dgi_channel", "custom_dgi_operation"):
            doc.set(f, frappe.db.get_value("Sales Invoice", doc.name, f))
        return
    if res.get("stage") == "queued-mcf":
        frappe.throw(_("Facture transmise au MCF (operation {0}, position {1} dans la file). Elle pourra etre "
                       "validee des reception des elements de securite.").format(res.get("operation"), res.get("position") or 1),
                     title=_("Normalisation MCF en cours"))
    frappe.throw(_("Soumission bloquee : {0}").format(res.get("message") or doc.get("custom_dgi_error") or "?"),
                 title=_("Normalisation DGI requise"))


def on_sales_invoice_submit(doc, method=None):
    """Normalization of a brand-new invoice submitted in one call (see before_sales_invoice_submit)."""
    if not doc.flags.get("dgi_normalize_on_submit"):
        return
    settings = get_settings()
    frappe.flags.dgi_no_commit = True
    try:
        res = normalize_invoice(doc, settings) or {}
    finally:
        frappe.flags.dgi_no_commit = False
    if doc.get("custom_dgi_status") != "Normalized":
        if res.get("stage") == "queued-mcf" or res.get("fallback_eligible"):
            frappe.throw(_("L'e-MCF est indisponible : ce point de vente passe sur son MCF de secours. Enregistrez "
                           "la facture en brouillon, puis validez-la."), title=_("Secours MCF"))
        frappe.throw(
            _("Soumission annulee: la facture n'a pas pu etre normalisee par la DGI.<br><b>{0}</b>").format(
                res.get("message") or doc.get("custom_dgi_error") or res.get("stage", "?")),
            title=_("Normalisation DGI requise"))


def before_sales_invoice_cancel(doc, method=None):
    """A normalized invoice is registered at the DGI: ERPNext cancellation is refused."""
    settings = get_settings()
    if not settings.enabled:
        return
    if _is_normalized(doc):
        frappe.throw(
            _("La facture {0} est normalisee aupres de la DGI (Code DEF/DGI {1}). Elle ne peut pas "
              "etre annulee dans ERPNext. Emettez une facture d'avoir (Retour / Note de credit, type "
              "FA ou EA) avec la nature 'RAN - Annulation' referencant cette facture.")
            .format(doc.name, doc.get("custom_dgi_code_def") or "-"),
            title=_("Annulation interdite"))


def on_sales_invoice_cancel(doc, method=None):
    """Only a non-normalized invoice reaches this point: release a pending e-MCF request."""
    settings = get_settings()
    if not settings.enabled:
        return
    if frappe.db.table_exists("DGI Fiscal Operation"):
        from dgi_compliance.fiscal.operations import cancel_queued_for_invoice
        cancel_queued_for_invoice(doc.name, _("Facture annulee"))
    if frappe.db.table_exists("DGI MCF Job"):
        from dgi_compliance.mcf.jobs import cancel_pending_jobs
        cancel_pending_jobs(doc.name)
    uid = doc.get("custom_dgi_uid")
    if uid and doc.get("custom_dgi_channel") != "MCF":
        res = client.cancel_invoice(uid)
        log_exchange("cancel-pending", {"uid": uid}, res.get("data"), res.get("error"),
                     status_code=res.get("status"), reference_invoice=doc.name)
    if doc.get("custom_dgi_status"):
        doc.db_set("custom_dgi_status", "Cancelled", update_modified=False)


def on_sales_invoice_trash(doc, method=None):
    """A draft that reached the e-MCF (UID or security elements) cannot be deleted."""
    settings = get_settings()
    if not settings.enabled:
        return
    if _is_normalized(doc) or doc.get("custom_dgi_uid"):
        frappe.throw(
            _("La facture {0} a ete transmise a la DGI (UID {1}): elle ne peut pas etre supprimee. "
              "Soumettez-la ou annulez la demande en attente.").format(
                doc.name, doc.get("custom_dgi_uid") or doc.get("custom_dgi_code_def")),
            title=_("Suppression interdite"))
    if frappe.db.table_exists("DGI Fiscal Operation"):
        from dgi_compliance.fiscal.operations import active_operation, cancel_queued_for_invoice
        op = active_operation(doc.name)
        if op and op.status != "Queued":
            frappe.throw(_("La facture {0} est en cours de normalisation sur le MCF (operation {1}) : elle ne peut "
                           "pas etre supprimee.").format(doc.name, op.name), title=_("Suppression interdite"))
        cancel_queued_for_invoice(doc.name, _("Facture supprimee"))
        # the operation keeps the invoice number (fn) for the audit trail; the link is released
        frappe.db.sql("update `tabDGI Fiscal Operation` set sales_invoice=NULL where sales_invoice=%s "
                      "and status in ('Cancelled', 'Rejected')", doc.name)


def protect_fiscal_fields(doc, method=None):
    """`before_update_after_submit`: the security elements cannot be edited by hand / API.
    v5.4.0: the sequential reference is owned by the SFE: the stored value is kept whatever is sent."""
    before = doc.get_doc_before_save()
    if not before:
        return
    doc.set("dgi_sequence_ref", before.get("dgi_sequence_ref"))
    changed = [f for f in FISCAL_FIELDS if (doc.get(f) or None) != (before.get(f) or None)]
    if changed:
        frappe.throw(_("Champs fiscaux DGI non modifiables: {0}.").format(", ".join(changed)),
                     title=_("Elements de securite proteges"))


# ---------------- whitelisted actions (buttons) ----------------

def _check_invoice_access(invoice):
    doc = frappe.get_doc("Sales Invoice", invoice)
    if not frappe.has_permission("Sales Invoice", "write", doc=doc):
        frappe.throw(_("Permission insuffisante sur cette facture."), frappe.PermissionError)
    return doc


@frappe.whitelist()
def normalize_sales_invoice(invoice: str) -> dict:
    """'Normaliser (DGI)' button on a draft: success unlocks the submission."""
    doc = _check_invoice_access(invoice)
    if doc.docstatus == 2:
        frappe.throw(_("Facture annulee: normalisation impossible."))
    settings = get_settings()
    if not settings.enabled:
        frappe.throw(_("DGI Compliance est desactive."))
    return normalize_invoice(doc, settings) or {"ok": True}


@frappe.whitelist()
def retry_normalization(invoice: str) -> dict:
    """'Re-tenter' button for a SUBMITTED invoice left in Error by v4 (gating 'Off')."""
    doc = _check_invoice_access(invoice)
    if doc.docstatus != 1:
        frappe.throw(_("La facture doit etre soumise."))
    settings = get_settings()
    if not settings.enabled:
        frappe.throw(_("DGI Compliance est desactive."))
    return normalize_invoice(doc, settings) or {"ok": True}


@frappe.whitelist()
def clear_uncertain_confirmation(invoice: str, verified_not_registered: int = 0) -> dict:
    """System Manager only: after checking on the DGI verification portal that the invoice was
    NOT registered, release the uncertain UID so that a new normalization can be sent."""
    frappe.only_for("System Manager")
    doc = frappe.get_doc("Sales Invoice", invoice)
    if not int(verified_not_registered or 0):
        frappe.throw(_("Confirmez d'abord sur le portail de verification DGI que la facture n'y figure pas."))
    if _is_normalized(doc):
        frappe.throw(_("Facture deja normalisee."))
    old = doc.get("custom_dgi_uid")
    _set(doc, {"custom_dgi_uid": None, "custom_dgi_status": "Pending", "custom_dgi_error": None})
    log_exchange("uncertain-cleared", {"invoice": invoice, "uid": old, "by": frappe.session.user},
                 None, reference_invoice=invoice)
    from dgi_compliance.fiscal.journal import close_uncertain_emcf
    close_uncertain_emcf(invoice, frappe.session.user)
    _commit()
    return {"ok": True}


# ---------------- core normalization ----------------

def _mark_error(doc, msg):
    msg = str(msg or "Erreur de normalisation")
    _set(doc, {"custom_dgi_status": "Error", "custom_dgi_error": msg[:140]})
    if len(msg) > 140:
        log_exchange("error-detail", {"invoice": doc.name}, None, msg, reference_invoice=doc.name)


def _fail(doc, stage, msg, notify=True):
    _mark_error(doc, msg)
    _commit()
    if notify:
        frappe.msgprint(msg, title=_("Normalisation DGI"), indicator="red")
    return {"ok": False, "stage": stage, "message": msg}


def _confirm_was_sent(doc, uid):
    return bool(frappe.db.exists("DGI Exchange Log", {
        "reference_invoice": doc.name, "direction": "confirm",
        "request_payload": ["like", f"%{uid}%"]}))


def _pending_details(uid):
    """(details dict or None, result) for a pending request."""
    res = client.invoice_details(uid)
    data = res.get("data") if isinstance(res.get("data"), dict) else None
    if res.get("ok") and data and not data.get("errorCode"):
        return data, res
    return None, res


def _find_pending_by_rn(rn):
    """Look for a pending e-MCF request already created for this SFE invoice number."""
    st = client.invoice_status()
    data = st.get("data") if isinstance(st.get("data"), dict) else {}
    for p in (data.get("pendingRequestsList") or [])[:10]:
        uid = p.get("uid")
        if not uid:
            continue
        details, _res = _pending_details(uid)
        if details and str(details.get("rn") or "") == str(rn):
            return uid, details
    return None, None


def _totals_from_details(details):
    totals = (details or {}).get("totals")
    return totals if isinstance(totals, dict) else (details or {})


def _reuse_or_create(doc, dto):
    """Return (uid, invoice_response, failure_dict). Reuses a pending request of this invoice
    instead of creating a second one."""
    stale = doc.get("custom_dgi_uid")
    if stale:
        details, res = _pending_details(stale)
        if details and str(details.get("rn") or doc.name) == doc.name:
            log_exchange("reuse-pending", {"uid": stale}, details, reference_invoice=doc.name)
            return stale, _totals_from_details(details), None
        if _confirm_was_sent(doc, stale):
            msg = (f"{UNCERTAIN_PREFIX}: la confirmation de la demande {stale} a ete envoyee sans "
                   "reponse exploitable. Verifiez sur le portail de verification DGI avant toute "
                   "nouvelle tentative (bouton 'Liberer la confirmation incertaine').")
            return None, None, {"stage": "uncertain", "message": msg}
        log_exchange("stale-uid", {"uid": stale}, res.get("data"), res.get("error"),
                     status_code=res.get("status"), reference_invoice=doc.name)
        _set(doc, {"custom_dgi_uid": None})

    created = client.create_invoice(dto)
    log_exchange("create", dto, created.get("data"), created.get("error"),
                 status_code=created.get("status"), url=created.get("url"), reference_invoice=doc.name)
    data = created.get("data") if isinstance(created.get("data"), dict) else {}
    if created.get("ok") and data.get("uid") and not data.get("errorCode"):
        return data["uid"], data, None

    if str(data.get("errorCode")) == "63":
        m = UUID_RE.search(str(data.get("errorDesc") or ""))
        if m:
            details, _res = _pending_details(m.group(0))
            if details and str(details.get("rn") or "") == doc.name:
                log_exchange("reuse-pending", {"uid": m.group(0), "via": "error 63"}, details,
                             reference_invoice=doc.name)
                return m.group(0), _totals_from_details(details), None

    if (created.get("status") or 0) == 0 or (created.get("status") or 0) >= 500:
        uid, details = _find_pending_by_rn(doc.name)
        if uid:
            log_exchange("reuse-pending", {"uid": uid, "via": "pending list"}, details,
                         reference_invoice=doc.name)
            return uid, _totals_from_details(details), None

    msg = describe(data, created.get("error") or _("Demande de facture refusee par l'e-MCF (HTTP {0}).")
                   .format(created.get("status")))
    if is_retryable(created):
        msg += " " + _("(erreur temporaire: reessayez)")
    from dgi_compliance.fiscal.fallback import unavailable
    return None, None, {"stage": "create", "message": msg,
                        "unavailable": unavailable(created) and not data.get("errorCode")}


def verify_amounts(doc, dto, resp, settings):
    """Differences between the SFE calculation and the e-MCF figures (empty list = identical)."""
    tol = flt(getattr(settings, "emcf_check_tolerance", None) or 1.0)
    rates = response_rates(resp)
    if not rates:
        from dgi_compliance.edef.mapper import group_rate
        rates = {it["taxGroup"]: group_rate(it["taxGroup"]) for it in dto.get("items") or []}
    expected = compute_totals(dto.get("items") or [], dto.get("mode"), rates)
    groups = response_groups(resp)
    actual = {"total": flt(resp.get("total")), "vtotal": flt(resp.get("vtotal")),
              "groups": {g: {"total": v["total"], "vat": v["vat"]} for g, v in groups.items()}}
    if not groups:
        expected = {"total": expected["total"], "vtotal": expected["vtotal"], "groups": {}}
    diffs = compare(expected, actual, tol)
    # Commercial coherence: e-MCF total vs ERPNext grand total (CDF).
    erp_total = abs(flt(doc.get("base_grand_total")))
    erp_tol = flt(settings.dgi_total_tolerance or 1.0)
    if abs(flt(resp.get("total")) - erp_total) > erp_tol:
        diffs.append(f"Total ERPNext={erp_total:.2f} CDF / e-MCF={flt(resp.get('total')):.2f} CDF "
                     f"(tolerance {erp_tol:g})")
    return diffs


def normalize_invoice(doc, settings=None):
    settings = settings or get_settings()

    if not doc.is_new():
        db = frappe.db.get_value("Sales Invoice", doc.name,
                                 ["custom_dgi_status", "custom_dgi_code_def"], as_dict=True)
        if db and db.custom_dgi_status == "Normalized" and db.custom_dgi_code_def:
            for f in _RESULT_FIELDS + ("custom_dgi_status",):
                doc.set(f, frappe.db.get_value("Sales Invoice", doc.name, f))
            return {"ok": True, "stage": "normalized", "uid": doc.get("custom_dgi_uid"),
                    "code_def": doc.get("custom_dgi_code_def")}

    # v5.3.0: point de vente, canal, equipement (fiscal.router) ; journal unique (fiscal.journal)
    from dgi_compliance.fiscal import fallback as FB
    from dgi_compliance.fiscal import journal as J
    from dgi_compliance.fiscal.router import CHANNEL_EMCF, CHANNEL_MCF, SECOURS_AUTO, RoutingError, route
    if not doc.is_new() and frappe.db.exists("Sales Invoice", doc.name):
        frappe.db.sql("select name from `tabSales Invoice` where name=%s for update", doc.name)
    try:
        fiscal_route = route(doc, settings, force_channel=frappe.flags.get("dgi_force_channel"))
    except RoutingError as e:
        return _fail(doc, "route", str(e))
    J.record_summary(doc, fiscal_route)
    if fiscal_route.point_of_sale and doc.get("dgi_point_of_sale") != fiscal_route.point_of_sale \
            and frappe.db.exists("Sales Invoice", doc.name):
        doc.db_set({"dgi_point_of_sale": fiscal_route.point_of_sale, "dgi_pos_auto": 1}, update_modified=False)
    busy = J.check_active(doc, fiscal_route.channel)
    if busy:
        frappe.msgprint(busy["message"], title=_("Normalisation DGI"), indicator="orange")
        return busy
    if fiscal_route.open_fallback:
        FB.activate(fiscal_route.point_of_sale, fiscal_route.open_fallback["reason"],
                    fiscal_route.open_fallback["from"], fiscal_route.open_fallback["to"])
    if fiscal_route.channel == CHANNEL_MCF:
        return _queue_mcf(doc, settings, fiscal_route, fallback_reason=fiscal_route.fallback_reason)

    # e-MCF; the MCF of the same point de vente takes over on a proven non-reception (fiscal.fallback)
    alternate = fiscal_route.alternate
    frappe.local.dgi_emcf_token = fiscal_route.emcf_token
    frappe.flags.dgi_fallback_possible = bool(alternate)
    op_name = J.start_emcf(doc, fiscal_route)
    try:
        res = _normalize_emcf(doc, settings)
    except Exception as e:
        J.finish_emcf(op_name, doc, {"ok": False, "stage": "exception", "message": str(e)})
        _commit()
        raise
    finally:
        frappe.local.dgi_emcf_token = None
        frappe.flags.dgi_fallback_possible = False
    J.finish_emcf(op_name, doc, res)
    _commit()
    if alternate and isinstance(res, dict) and res.get("fallback_eligible"):
        FB.activate(fiscal_route.point_of_sale, res.get("message"), CHANNEL_EMCF, CHANNEL_MCF)
        alternate.rules = list(fiscal_route.rules) + [SECOURS_AUTO]
        alternate.fallback_reason = res.get("message")
        alternate.decision = dict(fiscal_route.decision, channel=CHANNEL_MCF, rules=alternate.rules,
                                  fallback_reason=res.get("message"), equipment={"nid": alternate.device})
        J.record_summary(doc, alternate)
        return _queue_mcf(doc, settings, alternate, fallback_reason=res.get("message"))
    return res


def _queue_mcf(doc, settings, fiscal_route, fallback_reason=None):
    from dgi_compliance.fiscal.mcf_payload import MCFPayloadError
    from dgi_compliance.fiscal.operations import request_mcf_normalization
    try:
        res = request_mcf_normalization(doc, settings, fiscal_route, fallback_reason=fallback_reason)
    except MCFPayloadError as e:
        _mark_error(doc, str(e))
        _commit()
        raise
    _commit()
    return res


def _normalize_emcf(doc, settings):
    # 1. Request
    try:
        dto = build_invoice_request(doc)
    except (TaxGroupMappingError, PaymentTypeMappingError) as e:
        log_exchange("validate", {"invoice": doc.name}, None, str(e), reference_invoice=doc.name)
        _mark_error(doc, str(e))
        _commit()
        raise
    errors = validate_invoice_request(dto)
    if errors:
        log_exchange("validate", dto, None, errors, reference_invoice=doc.name)
        return _fail(doc, "validate", _("Donnees de facture non conformes: ") + "; ".join(errors))

    # 2. Pending request of this invoice, else a new one (UID persisted at once)
    uid, resp, failure = _reuse_or_create(doc, dto)
    if failure:
        if failure.get("unavailable") and frappe.flags.get("dgi_fallback_possible"):
            # no CONFIRM was ever sent for this invoice: a pending e-MCF request (if any) expires by itself
            log_exchange("fallback-mcf", {"invoice": doc.name}, None, failure["message"], reference_invoice=doc.name)
            return {"ok": False, "stage": failure["stage"], "message": failure["message"], "fallback_eligible": True}
        res = _fail(doc, failure["stage"], failure["message"])
        res["unavailable"] = bool(failure.get("unavailable"))
        return res
    _set(doc, {"custom_dgi_uid": uid})
    _commit()

    # 3. SFE check of the e-MCF amounts before CONFIRM
    diffs = verify_amounts(doc, dto, resp, settings)
    if diffs:
        log_exchange("check-amounts", {"uid": uid, "request": dto}, resp, "; ".join(diffs),
                     reference_invoice=doc.name)
        if (getattr(settings, "emcf_check_action", None) or "Bloquer") != "Alerte":
            cancelled = client.cancel_invoice(uid)
            log_exchange("cancel", {"uid": uid, "reason": "montants divergents"}, cancelled.get("data"),
                         cancelled.get("error"), status_code=cancelled.get("status"), reference_invoice=doc.name)
            _set(doc, {"custom_dgi_uid": None})
            return _fail(doc, "check-amounts",
                         _("Montants e-MCF differents du calcul SFE, demande annulee: ") + "; ".join(diffs))
        frappe.msgprint(_("Avertissement: ") + "; ".join(diffs), indicator="orange", title=_("DGI"))

    if settings.reconcile_with_emcf and (settings.vat_accounts or []):
        erp_vat = abs(doc_vat_base(doc, settings))
        if abs(flt(erp_vat) - flt(resp.get("vtotal"))) > flt(settings.reconcile_tolerance or 0):
            frappe.msgprint(_("Avertissement TVA DGI - ERPNext={0} / e-MCF={1}").format(
                erp_vat, flt(resp.get("vtotal"))), indicator="orange")

    # 4. CONFIRM
    total, vtotal = flt(resp.get("total")), flt(resp.get("vtotal"))
    confirmed = client.confirm_invoice(uid, total, vtotal)
    log_exchange("confirm", {"uid": uid, "total": total, "vtotal": vtotal}, confirmed.get("data"),
                 confirmed.get("error"), status_code=confirmed.get("status"), reference_invoice=doc.name)
    cdata = confirmed.get("data") if isinstance(confirmed.get("data"), dict) else {}
    if not confirmed.get("ok") or cdata.get("errorCode") or not cdata.get("codeDEFDGI"):
        status = confirmed.get("status") or 0
        if status == 0 or status >= 500:
            return _fail(doc, "confirm",
                         f"{UNCERTAIN_PREFIX}: pas de reponse de l'e-MCF a la confirmation de {uid}. "
                         "Ne renvoyez pas la facture avant d'avoir verifie sur le portail DGI.")
        cancelled = client.cancel_invoice(uid)
        log_exchange("cancel", {"uid": uid, "reason": "confirm refuse"}, cancelled.get("data"),
                     cancelled.get("error"), status_code=cancelled.get("status"), reference_invoice=doc.name)
        _set(doc, {"custom_dgi_uid": None})
        return _fail(doc, "confirm", describe(cdata, confirmed.get("error") or "Confirmation refusee"))

    op = dto.get("operator") or {}
    _set(doc, {
        "custom_dgi_status": "Normalized",
        "custom_dgi_channel": "e-MCF",
        "custom_dgi_code_def": cdata.get("codeDEFDGI"),
        "custom_dgi_counters": cdata.get("counters"),
        "custom_dgi_nim": cdata.get("nim"),
        "custom_dgi_datetime": cdata.get("dateTime"),
        "custom_dgi_qr_code": cdata.get("qrCode"),
        "custom_dgi_qr_image": make_qr_data_uri(cdata.get("qrCode")),
        "custom_dgi_error": None,
        "custom_dgi_normalized_on": now_datetime(),
        "custom_dgi_operator": op.get("id"),
        "custom_dgi_operator_name": op.get("name"),
        "custom_dgi_payload_hash": payload_hash(dto),
        "custom_dgi_response": json.dumps({"request": dto, "invoice": resp, "confirm": cdata},
                                          ensure_ascii=False, default=str),
    })
    # v5.4.0: sequential reference of the point de vente, given as soon as the security elements are committed
    from dgi_compliance.fiscal.sequence import assign_when_committed
    assign_when_committed(doc.name, source="e-MCF", operation=doc.get("custom_dgi_operation"), doc=doc)
    _commit()

    total_diff = total - abs(flt(doc.base_grand_total))
    if settings.auto_post_exchange_diff and total_diff and abs(total_diff) <= flt(settings.dgi_total_tolerance or 1):
        je = _post_exchange_difference(doc, settings, abs(flt(doc.base_grand_total)), total, total_diff)
        if je:
            _set(doc, {"custom_dgi_exchange_je": je})
            _commit()

    return {"ok": True, "stage": "normalized", "uid": uid, "code_def": cdata.get("codeDEFDGI")}


def _post_exchange_difference(doc, settings, erp_total, dgi_total, diff):
    """Journal Entry for the small ERPNext-vs-DGI total difference (optional)."""
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
        if diff > 0:
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


# ---------------- scheduled token monitoring ----------------

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
    if settings.is_mcf_mode() or not settings.enabled or not _should_run_today(settings):
        return
    res = client.info_status()
    data = res.get("data") or {}
    log_exchange("token-check", None, data, res.get("error"), status_code=res.get("status"))
    if not res.get("ok"):
        _notify(settings, "ALERTE jeton e-MCF: /api/info/status en echec",
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
            _notify(settings, f"Jeton e-MCF expire dans {days_left} jour(s)",
                    f"Le jeton e-MCF (env. {settings.environment}) expire le {token_valid}. "
                    "Regenerez-le sur le portail e-MCF et mettez a jour DGI Compliance Settings.")
    if data.get("status") is False:
        _notify(settings, "API e-MCF indisponible", "GET /api/info/status renvoie status=false.")


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
