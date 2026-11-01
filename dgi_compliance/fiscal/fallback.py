"""Automatic fallback between the two fiscal channels, in both directions (v5.3.0, livrable B section 5).

Decisions of 31/10 and 01/11/2026: e-MCF prioritaire by default, MCF only when the e-MCF does not
work; when an administrator puts the MCF first, the e-MCF becomes the fallback (symmetric).

Proof of non-reception, the only trigger
  * e-MCF -> MCF: failure before any CONFIRM was sent (network, DNS, timeout, HTTP 5xx/401/403/408/429
    on the request). A pending e-MCF request never confirmed cannot become a normalized invoice: it
    expires after 2 minutes (API e-MCF v1.3).
  * MCF -> e-MCF: the station of the MCF is silent at routing time (no operation is created on the
    MCF), or an operation stayed Queued longer than `mcf_fallback_after_s` while the station took
    nothing: it is withdrawn from the queue under row lock, which proves that the station never got it.
  * Never from: CONFIRM sent without answer, operation leased by the station, Checking, Uncertain,
    Dispute. An explicit refusal does not switch automatically (the other channel would probably refuse
    the same data); a DGI Fiscal Manager may send a rejected operation to the other channel.
The first proven failure opens the fallback of the point de vente: following invoices go to the
fallback channel at once. Every minute, after `emcf_retry_minutes`, the scheduler tests the prioritized
channel without fiscal effect (e-MCF: GET /api/info/status with the PVF token; MCF: recent signal of
the station and readable MCF state) and closes the fallback when it answers; operations still queued
for the MCF fallback (never taken by the station) go back to the e-MCF.

The open/closed state lives in Redis (outside the database transaction): an invoice whose own
transaction is rolled back still opens the fallback for the next ones. `fallback_since` /
`fallback_reason` of DGI Point of Sale mirror it for the desk.
"""
import json

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

from dgi_compliance.fiscal.router import (CHANNEL_EMCF, CHANNEL_MCF, POLICY_AUTO, Route, equipped,
                                          mcf_reachable, other_channel)

DOCTYPE = "DGI Fiscal Operation"


def _key(pvf):
    return frappe.cache.make_key(f"dgi_fallback|{pvf}", shared=True)


def _settings():
    from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings
    return get_settings()


def state(pvf: str) -> dict | None:
    if not pvf:
        return None
    raw = frappe.cache.get(_key(pvf))
    if not raw:
        return None
    try:
        st = json.loads(raw if isinstance(raw, str) else raw.decode())
    except (ValueError, AttributeError):
        return None
    st.setdefault("from", CHANNEL_EMCF)              # v5.2 states: e-MCF -> MCF
    st.setdefault("to", other_channel(st["from"]))
    return st


def is_active(pvf: str) -> bool:
    return state(pvf) is not None


def fallback_route(route: Route) -> Route | None:
    """Compatibility (v5.2): the route of the other channel when the automatic fallback applies."""
    if not route:
        return None
    if route.alternate:
        return route.alternate
    if route.channel != CHANNEL_EMCF or not route.point_of_sale:
        return None
    pvf = frappe.get_doc("DGI Point of Sale", route.point_of_sale)
    if (pvf.get("fallback_policy") or POLICY_AUTO) != POLICY_AUTO:
        return None
    ok, _why, _eq = equipped(pvf, CHANNEL_MCF)
    if not ok:
        return None
    return Route(route.point_of_sale, CHANNEL_MCF, device=pvf.mcf_device, nif=route.nif)


def activate(pvf: str, reason: str, from_channel: str = CHANNEL_EMCF, to_channel: str | None = None) -> None:
    from dgi_compliance.gateway.events import log_event
    if is_active(pvf):
        return
    to_channel = to_channel or other_channel(from_channel)
    since = now_datetime()
    frappe.cache.set(_key(pvf), json.dumps({"since": str(since), "reason": (reason or "")[:300],
                                            "from": from_channel, "to": to_channel}))
    code = "SECOURS_MCF" if to_channel == CHANNEL_MCF else "SECOURS_EMCF"
    log_event(None, code, _("{0} : {1} indisponible, les factures partent sur le canal de secours {2} ({3}).")
              .format(pvf, from_channel, to_channel, reason or "?"), level="Critique", point_of_sale=pvf,
              details={"from": from_channel, "to": to_channel})
    _mirror(pvf, since, f"{from_channel} -> {to_channel} : {reason or ''}")


def deactivate(pvf: str, why: str) -> int:
    """Close the fallback. When it went to the MCF, the invoices still queued for it go back to the
    e-MCF. Returns their count."""
    from dgi_compliance.gateway.events import log_event
    st = state(pvf) or {"from": CHANNEL_EMCF, "to": CHANNEL_MCF}
    frappe.cache.delete(_key(pvf))
    _mirror(pvf, None, None)
    moved = reroute_queued_to_emcf(pvf) if st.get("to") == CHANNEL_MCF else 0
    code = "RETOUR_EMCF" if st.get("from") == CHANNEL_EMCF else "RETOUR_MCF"
    log_event(None, code, _("{0} : {1} retabli ({2}) ; {3} facture(s) en file renvoyee(s) a l'e-MCF.")
              .format(pvf, st.get("from"), why, moved), level="Alerte", point_of_sale=pvf)
    return moved


def _mirror(pvf, since, reason):
    try:
        frappe.db.set_value("DGI Point of Sale", pvf, {"fallback_since": since,
                                                      "fallback_reason": (reason or "")[:500] or None},
                            update_modified=False)
        frappe.clear_document_cache("DGI Point of Sale", pvf)
    except Exception:
        pass


def _withdraw(op, reason) -> bool:
    """Queued -> Cancelled under row lock. True when this call withdrew it (the station never got it)."""
    from dgi_compliance.fiscal.operations import _publish
    frappe.db.sql(f"select name from `tab{DOCTYPE}` where name=%s for update", op.name)
    op.reload()
    if op.status != "Queued":
        return False
    op.db_set({"status": "Cancelled", "finished_at": now_datetime(), "error_message": reason[:500]})
    _publish(op)
    if op.sales_invoice:
        frappe.db.set_value("Sales Invoice", op.sales_invoice,
                            {"custom_dgi_status": "Pending", "custom_dgi_error": None,
                             "custom_dgi_operation": None, "custom_dgi_channel": None}, update_modified=False)
        frappe.enqueue("dgi_compliance.fiscal.fallback.renormalize", invoice=op.sales_invoice,
                       queue="short", enqueue_after_commit=True)
    return True


def reroute_queued_to_emcf(pvf: str) -> int:
    """Fallback operations never sent to the MCF (Queued) are cancelled; their invoices are normalized
    again through the e-MCF in the background."""
    n = 0
    for name in frappe.get_all(DOCTYPE, filters={"point_of_sale": pvf, "status": "Queued", "fallback": 1,
                                                 "channel": CHANNEL_MCF}, pluck="name"):
        if _withdraw(frappe.get_doc(DOCTYPE, name), _("e-MCF retabli : renvoyee a l'e-MCF.")):
            n += 1
    return n


def renormalize(invoice: str):
    from dgi_compliance.edef.tasks import normalize_invoice
    doc = frappe.get_doc("Sales Invoice", invoice)
    if doc.docstatus == 2 or doc.get("custom_dgi_status") == "Normalized":
        return
    normalize_invoice(doc)


def send_stale_mcf_queue_to_emcf() -> int:
    """Scheduler: MCF prioritaire, the station took nothing for `mcf_fallback_after_s` -> the waiting
    invoices are withdrawn from the queue (proof: never leased) and sent to the e-MCF."""
    settings = _settings()
    delay = max(15, cint(settings.get("mcf_fallback_after_s") or 60))
    limit = add_to_date(now_datetime(), seconds=-delay)
    moved = 0
    rows = frappe.get_all(DOCTYPE, filters={"status": "Queued", "channel": CHANNEL_MCF, "fallback": 0,
                                            "queued_at": ["<", limit]},
                          fields=["name", "point_of_sale", "mcf_device"], order_by="queued_at asc")
    busy = {}
    for row in rows:
        if not row.point_of_sale:
            continue
        pvf = frappe.get_doc("DGI Point of Sale", row.point_of_sale)
        if (pvf.get("fallback_policy") or POLICY_AUTO) != POLICY_AUTO \
                or not equipped(pvf, CHANNEL_EMCF, settings, fallback=True)[0]:
            continue
        if row.mcf_device not in busy:     # the station is working through the queue: no fallback
            busy[row.mcf_device] = bool(frappe.db.exists(DOCTYPE, {"mcf_device": row.mcf_device,
                                                                   "leased_at": [">", limit]}))
        if busy[row.mcf_device]:
            continue
        reason = _("facture en file au MCF depuis plus de {0} s sans prise par le poste").format(delay)
        frappe.db.sql(f"select name from `tab{DOCTYPE}` where name=%s for update", row.name)
        if frappe.db.get_value(DOCTYPE, row.name, "status") != "Queued":
            continue
        activate(row.point_of_sale, reason, CHANNEL_MCF, CHANNEL_EMCF)
        if _withdraw(frappe.get_doc(DOCTYPE, row.name),
                     _("Retiree de la file du MCF (jamais prise par le poste) : envoyee a l'e-MCF.")):
            moved += 1
    if moved:
        frappe.db.commit()
    return moved


def probe_all():
    """Scheduler (every minute): close the fallbacks whose prioritized channel answers again."""
    retry = max(1, cint(_settings().get("emcf_retry_minutes") or 5))
    limit = add_to_date(now_datetime(), minutes=-retry)
    for pvf in frappe.get_all("DGI Point of Sale", filters={"is_active": 1}, pluck="name"):
        st = state(pvf)
        if not st:
            if frappe.db.get_value("DGI Point of Sale", pvf, "fallback_since"):
                _mirror(pvf, None, None)            # Redis flushed: the desk follows the real state
            continue
        try:
            if get_datetime(st.get("since")) > limit:
                continue
        except Exception:
            pass
        back = emcf_available(pvf) if st.get("from") == CHANNEL_EMCF else mcf_available(pvf)
        if back:
            deactivate(pvf, _("test automatique du canal {0}").format(st.get("from")))
            frappe.db.commit()


def emcf_available(pvf: str) -> bool:
    from dgi_compliance.edef import client
    doc = frappe.get_doc("DGI Point of Sale", pvf)
    try:
        frappe.local.dgi_emcf_token = doc.get_password("token", raise_exception=False) or None
    except Exception:
        frappe.local.dgi_emcf_token = None
    try:
        res = client.call("GET", "/api/info/status", retries=0)
    finally:
        frappe.local.dgi_emcf_token = None
    data = res.get("data") if isinstance(res.get("data"), dict) else {}
    return bool(res.get("ok")) and data.get("status") is not False


def mcf_available(pvf: str) -> bool:
    """MCF back: Actif, station signal recent, state of the MCF read recently (no fiscal effect)."""
    doc = frappe.get_doc("DGI Point of Sale", pvf)
    ok, _why, _eq = equipped(doc, CHANNEL_MCF)
    if not ok:
        return False
    reachable, _w = mcf_reachable(doc.mcf_device)
    if not reachable:
        return False
    minutes = max(1, cint(_settings().get("gateway_offline_minutes") or 2))
    seen = frappe.db.get_value("DGI MCF Device", doc.mcf_device, "last_status_on")
    return bool(seen) and get_datetime(seen) >= add_to_date(now_datetime(), minutes=-minutes)


def unavailable(res: dict) -> bool:
    """e-MCF failure that proves the service did not take the request into account."""
    status = int(res.get("status") or 0)
    return status == 0 or status >= 500 or status in (401, 403, 408, 429)


@frappe.whitelist(methods=["POST"])
def close_fallback(point_of_sale: str) -> dict:
    """Desk button: return to the prioritized channel now (DGI Fiscal Manager)."""
    frappe.only_for(("System Manager", "DGI Fiscal Manager"))
    if not is_active(point_of_sale):
        return {"active": False, "moved": 0}
    moved = deactivate(point_of_sale, _("retour manuel par {0}").format(frappe.session.user))
    return {"active": False, "moved": moved}


@frappe.whitelist()
def fallback_state(point_of_sale: str) -> dict:
    if not frappe.has_permission("DGI Point of Sale", "read", point_of_sale):
        frappe.throw(_("Permission insuffisante."), frappe.PermissionError)
    return state(point_of_sale) or {}
