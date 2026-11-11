"""Desk API of the fiscal operations (buttons of Sales Invoice, DGI Point of Sale, DGI Fiscal Operation)."""
import uuid

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

from dgi_compliance.fiscal.operations import DOCTYPE, _publish, queue_position

MANAGERS = ("System Manager", "DGI Fiscal Manager")


@frappe.whitelist()
def operation_status(invoice: str) -> dict:
    if not frappe.has_permission("Sales Invoice", "read", invoice):
        frappe.throw(_("Permission insuffisante."), frappe.PermissionError)
    name = frappe.db.get_value("Sales Invoice", invoice, "custom_dgi_operation")
    if not name:
        return {}
    op = frappe.get_doc(DOCTYPE, name)
    return {"operation": op.name, "status": op.status, "error": op.error_message, "fallback": op.get("fallback"),
            "position": queue_position(op) if op.status == "Queued" else None,
            "invoice_status": frappe.db.get_value("Sales Invoice", invoice, "custom_dgi_status")}


@frappe.whitelist(methods=["POST"])
def cancel_queued(operation: str) -> dict:
    op = frappe.get_doc(DOCTYPE, operation)
    if not (frappe.has_permission("Sales Invoice", "write", op.sales_invoice) or set(frappe.get_roles()) & set(MANAGERS)):
        frappe.throw(_("Permission insuffisante."), frappe.PermissionError)
    from dgi_compliance.fiscal.operations import withdraw_queued
    op = withdraw_queued(op.name, _("Annulee par {0}").format(frappe.session.user))      # under row lock (v5.4.1)
    if not op:
        frappe.throw(_("Seule une operation encore en file (jamais transmise au MCF) peut etre annulee."))
    frappe.db.set_value("Sales Invoice", op.sales_invoice, {"custom_dgi_status": "Pending", "custom_dgi_error": None},
                        update_modified=False)
    _publish(op)
    return {"status": op.status}


@frappe.whitelist(methods=["POST"])
def resolve_dispute(operation: str, decision: str, justification: str) -> dict:
    """Close a dispute AFTER checking the DGI verification portal. Only 'Rejected' (the invoice is
    NOT registered at the DGI) is supported here; a registered invoice is settled by the
    reconciliation of the gateway, never by typing security elements by hand."""
    frappe.only_for(MANAGERS)
    from dgi_compliance.gateway.events import log_event
    op = frappe.get_doc(DOCTYPE, operation)
    if op.status != "Dispute":
        frappe.throw(_("L'operation n'est pas en litige."))
    if decision != "Rejected" or len((justification or "").strip()) < 10:
        frappe.throw(_("Decision non prise en charge ou justification trop courte."))
    op.db_set({"status": "Rejected", "finished_at": now_datetime(), "error_code": "LITIGE_CLOS",
               "error_message": _("Litige clos par {0} : {1}").format(frappe.session.user, justification)[:500]})
    frappe.db.set_value("Sales Invoice", op.sales_invoice, {"custom_dgi_status": "Pending", "custom_dgi_error": None},
                        update_modified=False)
    log_event(op.gateway, "LITIGE_CLOS", f"{op.name} : {justification}", level="Alerte",
              point_of_sale=op.point_of_sale, device=op.mcf_device)
    _publish(op)
    return {"status": op.status}


@frappe.whitelist(methods=["POST"])
def request_resync(point_of_sale: str) -> dict:
    """Exigence SFE 6.2: restart the synchronization of the MCF with the DGI (C2h RESTART)."""
    frappe.only_for(MANAGERS + ("Accounts Manager",))
    pvf = frappe.get_doc("DGI Point of Sale", point_of_sale)
    if not pvf.mcf_device:
        frappe.throw(_("Aucun MCF rattache."))
    op = frappe.get_doc({"doctype": DOCTYPE, "operation_id": str(uuid.uuid4()), "op_type": "RESYNC",
                         "status": "Queued", "channel": "MCF", "company": pvf.company, "point_of_sale": pvf.name,
                         "mcf_device": pvf.mcf_device, "attempt": 1, "queued_at": now_datetime(),
                         "requested_by": frappe.session.user}).insert(ignore_permissions=True)
    return {"operation": op.name}


@frappe.whitelist()
def device_status(point_of_sale: str) -> dict:
    if not frappe.has_permission("DGI Point of Sale", "read", point_of_sale):
        frappe.throw(_("Permission insuffisante."), frappe.PermissionError)
    pvf = frappe.get_doc("DGI Point of Sale", point_of_sale)
    if not pvf.mcf_device:
        return {}
    dev = frappe.get_doc("DGI MCF Device", pvf.mcf_device)
    gw = frappe.db.get_value("DGI Gateway", {"mcf_device": dev.name, "status": "Active"},
                             ["name", "last_seen", "gateway_version"], as_dict=True) or {}
    from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings
    minutes = max(1, cint(get_settings().get("gateway_offline_minutes") or 2))
    online = bool(gw.get("last_seen")) and get_datetime(gw["last_seen"]) >= add_to_date(now_datetime(), minutes=-minutes)
    waiting = frappe.db.count(DOCTYPE, {"mcf_device": dev.name, "status": ["in", ["Queued", "Leased", "Uncertain", "Checking"]]})
    from dgi_compliance.fiscal.fallback import state as fallback_state
    return {"nid": dev.nid, "statut": dev.status, "mid": dev.last_mid, "gateway": gw.get("name"),
            "secours_mcf_actif": bool(fallback_state(point_of_sale)),
            "gateway_online": online, "last_seen": gw.get("last_seen"), "gateway_version": gw.get("gateway_version"),
            "derniere_synchro_dgi": dev.dgi_last_sync, "alerte_synchro": dev.sync_alert,
            "transactions_en_attente_dgi": dev.dgi_pending, "drapeaux": dev.status_flags,
            "operations_en_cours": waiting}
