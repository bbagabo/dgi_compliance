"""Journal unique des transmissions (v5.3.0, livrable B sections 6 and 9).

DGI Fiscal Operation records every attempt, whatever the channel. The MCF operations are created by
fiscal.operations (queue of the station); this module records the e-MCF attempts, enforces "one
active operation per invoice, all channels together" and writes the routing decision on the invoice.

e-MCF states: Pending (request created, no CONFIRM sent) -> Normalized | Rejected | Cancelled
(proven non-reception, the pending request expires by itself) | Uncertain (CONFIRM without answer:
never another channel, release by a responsible after checking the DGI portal).
"""
import json
import uuid

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from dgi_compliance.fiscal.router import (ARBITRAGE, CHANNEL_EMCF, CHANNEL_MCF, EMISSION_CENTRALISEE, Route,
                                          decision_fields, explain)

DOCTYPE = "DGI Fiscal Operation"
EMCF_ACTIVE = ("Pending", "Uncertain")


def _last_operation(invoice):
    rows = frappe.get_all(DOCTYPE, filters={"sales_invoice": invoice}, fields=["name", "attempt"],
                          order_by="creation desc", limit=1)
    return rows[0] if rows else None


def record_summary(doc, route: Route):
    """Plain-language decision on the invoice (read-only field, allowed after submit)."""
    text = route.explanation()[:2000]
    if doc.get("dgi_routing_summary") != text and frappe.db.exists("Sales Invoice", doc.name):
        doc.db_set("dgi_routing_summary", text, update_modified=False)
    else:
        doc.dgi_routing_summary = text


def note_events(doc, route: Route, operation=None):
    from dgi_compliance.gateway.events import log_event
    decision = route.decision or {}
    if decision.get("pvf_rule") == ARBITRAGE:
        log_event(None, "ARBITRAGE_PVF", _("Facture {0} : point de vente {1} tranche par {2} ({3}).").format(
            doc.name, route.point_of_sale, doc.get("dgi_pos_arbitrated_by") or "?",
            (doc.get("dgi_pos_arbitration") or "")[:200]), level="Alerte", point_of_sale=route.point_of_sale,
            details={"operation": operation, "sources": decision.get("sources")})
    for w in decision.get("warnings") or []:
        if str(w).startswith(EMISSION_CENTRALISEE):
            log_event(None, EMISSION_CENTRALISEE, _("Facture {0} : {1}").format(doc.name, w), level="Info",
                      point_of_sale=route.point_of_sale, details={"operation": operation})


def check_active(doc, channel: str) -> dict | None:
    """Rule 1: one active operation per invoice, all channels together. Returns a result dict when the
    invoice must NOT be sent on `channel` now, None when it may (after withdrawing what was never received)."""
    from dgi_compliance.fiscal.operations import ACTIVE, _publish
    rows = frappe.get_all(DOCTYPE, filters={"sales_invoice": doc.name, "status": ["in", list(ACTIVE)]},
                          fields=["name", "status", "channel", "confirm_sent"], order_by="creation desc")
    for row in rows:
        row_channel = row.channel or CHANNEL_MCF
        if row_channel == channel:
            continue                            # same channel: the channel's own logic reuses it
        frappe.db.sql(f"select name from `tab{DOCTYPE}` where name=%s for update", row.name)
        op = frappe.get_doc(DOCTYPE, row.name)
        if row_channel == CHANNEL_MCF and op.status == "Queued":
            op.db_set({"status": "Cancelled", "finished_at": now_datetime(),
                       "error_message": _("Retiree de la file du MCF (jamais prise) : facture envoyee sur {0}.")
                       .format(channel)})
            _publish(op)
            continue
        if row_channel == CHANNEL_EMCF and op.status == "Pending" and not cint(op.confirm_sent):
            op.db_set({"status": "Cancelled", "finished_at": now_datetime(),
                       "error_message": _("Demande e-MCF jamais confirmee (elle expire seule) : facture envoyee sur {0}.")
                       .format(channel)})
            continue
        msg = _("La facture a deja une tentative {0} a l'etat {1} ({2}) : aucune autre tentative n'est permise "
                "tant que son issue n'est pas connue.").format(row_channel, op.status, op.name)
        return {"ok": False, "stage": "operation-active", "operation": op.name, "status": op.status,
                "channel": row_channel, "message": msg}
    return None


def start_emcf(doc, route: Route) -> str:
    """Create (or reuse) the e-MCF operation of this invoice. Returns its name."""
    values = decision_fields(route)
    values.update({"nim": route.nim, "point_of_sale": route.point_of_sale,
                   "fallback": 1 if route.fallback_reason else 0,
                   "fallback_reason": (route.fallback_reason or "")[:500] or None})
    existing = frappe.db.get_value(DOCTYPE, {"sales_invoice": doc.name, "channel": CHANNEL_EMCF,
                                             "status": ["in", list(EMCF_ACTIVE)]}, "name")
    if existing:
        frappe.db.set_value(DOCTYPE, existing, values, update_modified=False)
        name = existing
    else:
        last = _last_operation(doc.name)
        op = frappe.get_doc(dict(values, **{
            "doctype": DOCTYPE, "operation_id": str(uuid.uuid4()), "op_type": "NORMALIZE", "status": "Pending",
            "channel": CHANNEL_EMCF, "sales_invoice": doc.name, "company": doc.get("company"),
            "attempt": cint(last.attempt) + 1 if last else 1, "previous_operation": last.name if last else None,
            "queued_at": now_datetime(), "requested_by": frappe.session.user,
        })).insert(ignore_permissions=True)
        name = op.name
    if frappe.db.exists("Sales Invoice", doc.name):
        doc.db_set({"custom_dgi_operation": name}, update_modified=False)
    note_events(doc, route, name)
    return name


def finish_emcf(name: str, doc, res: dict | None):
    """Close the e-MCF operation from the outcome of edef.tasks._normalize_emcf."""
    from dgi_compliance.edef.tasks import UNCERTAIN_PREFIX, _confirm_was_sent
    if not name or not frappe.db.exists(DOCTYPE, name):
        return
    res = res if isinstance(res, dict) else {}
    db = frappe.db.get_value("Sales Invoice", doc.name, ["custom_dgi_status", "custom_dgi_uid", "custom_dgi_code_def",
                                                         "custom_dgi_nim", "custom_dgi_counters"], as_dict=True) or {}
    uid = res.get("uid") or db.get("custom_dgi_uid") or doc.get("custom_dgi_uid")
    message = str(res.get("message") or "")
    values = {"emcf_uid": uid or None, "confirm_sent": 1 if uid and _confirm_was_sent(doc, uid) else 0}
    if db.get("custom_dgi_status") == "Normalized" and db.get("custom_dgi_code_def"):
        values.update({"status": "Normalized", "finished_at": now_datetime(), "error_message": None,
                       "result": json.dumps({"code_def": db.get("custom_dgi_code_def"), "nim": db.get("custom_dgi_nim"),
                                             "counters": db.get("custom_dgi_counters")}, ensure_ascii=False)})
    elif res.get("fallback_eligible") or res.get("unavailable"):
        values.update({"status": "Cancelled", "finished_at": now_datetime(), "error_code": "NON_RECUE",
                       "error_message": (_("Non recue par l'e-MCF (panne prouvee avant confirmation) : ") + message)[:500]})
    elif message.startswith(UNCERTAIN_PREFIX):
        values.update({"status": "Uncertain", "error_code": "CONFIRMATION_INCERTAINE", "error_message": message[:500],
                       "confirm_sent": 1})
    else:
        values.update({"status": "Rejected", "finished_at": now_datetime(),
                       "error_code": (res.get("stage") or "REFUS")[:60], "error_message": message[:500] or None})
    frappe.db.set_value(DOCTYPE, name, values, update_modified=False)


def close_uncertain_emcf(invoice: str, by: str):
    for name in frappe.get_all(DOCTYPE, filters={"sales_invoice": invoice, "channel": CHANNEL_EMCF,
                                                 "status": ["in", list(EMCF_ACTIVE)]}, pluck="name"):
        frappe.db.set_value(DOCTYPE, name, {"status": "Cancelled", "finished_at": now_datetime(),
                                            "error_message": _("Verifiee non enregistree sur le portail DGI par {0}.")
                                            .format(by)}, update_modified=False)


# --------------------------------------------------------------------------- invoice validate hook
def validate_routing_fields(doc, method=None):
    """`validate` of Sales Invoice: arbitration rights, PVF filled in by ERPNext, decision preview.
    Never blocks the draft: contradictions are shown here and block at submission."""
    from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings
    from dgi_compliance.fiscal.router import RoutingError, is_fiscal_manager, resolve, route
    settings = get_settings()
    if not settings.enabled or doc.docstatus != 0:
        return
    arbitration_changed = doc.is_new() and bool(doc.get("dgi_pos_arbitration")) \
        or (not doc.is_new() and doc.has_value_changed("dgi_pos_arbitration"))
    if arbitration_changed:
        if (doc.get("dgi_pos_arbitration") or "").strip():
            if not is_fiscal_manager():
                frappe.throw(_("Seul un DGI Fiscal Manager peut arbitrer le point de vente fiscal d'une facture."),
                             frappe.PermissionError)
            if not doc.get("dgi_point_of_sale"):
                frappe.throw(_("Choisissez le point de vente fiscal retenu avant de saisir le motif d'arbitrage."))
            doc.dgi_pos_arbitrated_by = frappe.session.user
            doc.dgi_pos_auto = 0
        else:
            doc.dgi_pos_arbitration, doc.dgi_pos_arbitrated_by = None, None
    elif doc.get("dgi_pos_arbitration") and not doc.is_new() and doc.has_value_changed("dgi_point_of_sale") \
            and not is_fiscal_manager():
        doc.dgi_pos_arbitration, doc.dgi_pos_arbitrated_by = None, None
    if not doc.is_new() and doc.has_value_changed("dgi_point_of_sale") and not doc.flags.get("dgi_autofill"):
        doc.dgi_pos_auto = 0
    if doc.get("custom_dgi_status") == "Normalized":
        return
    res = resolve(doc, settings)
    if res["blocked"]:
        doc.dgi_routing_summary = _("Validation bloquee ({0}) : {1}").format(res["blocked"]["code"],
                                                                              res["blocked"]["message"])[:2000]
        frappe.msgprint(res["blocked"]["message"], title=_("Point de vente fiscal"), indicator="orange")
        return
    if res["pvf"] and (not doc.get("dgi_point_of_sale") or cint(doc.get("dgi_pos_auto"))):
        doc.dgi_point_of_sale, doc.dgi_pos_auto = res["pvf"], 1
    elif not res["pvf"] and cint(doc.get("dgi_pos_auto")):
        doc.dgi_point_of_sale, doc.dgi_pos_auto = None, 0
    try:
        doc.dgi_routing_summary = (_("Prevision : ") + route(doc, settings, strict=False).explanation())[:2000]
    except RoutingError as e:
        doc.dgi_routing_summary = (_("Prevision : bloquee ({0}) : ").format(e.code) + str(e))[:2000]
    except Exception:
        doc.dgi_routing_summary = (_("Prevision : ") + explain({"pvf": res["pvf"], "pvf_rule": res["rule"],
                                                                "sources": res["sources"]}))[:2000]
