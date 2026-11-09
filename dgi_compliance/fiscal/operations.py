"""Fiscal operations of the MCF channel: queue, lease, barrier, results, reconciliation.

Rules (HL2 architecture, sections 6 and 8):
  * one active operation per invoice; channel and device frozen when the operation is created;
  * one leased operation per device at a time; while an operation of a device is Leased,
    Uncertain or Checking, nothing else is leased for that device (barrier);
  * an expired lease makes the operation Uncertain, NEVER Queued again; it is then leased as a
    CHECK so that the gateway settles it through C1h;
  * a 'normalized' result is a fiscal fact: it is accepted whenever its content matches the
    operation (NID, NIF, invoice number, SIG format), even late; a 'rejected' result is only
    accepted with the current lease token;
  * an operation proven not normalized and retryable is re-queued automatically (bounded).
"""
import json
import secrets
import uuid

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, flt, get_datetime, now_datetime

from dgi_compliance.fiscal import mcf_format as F
from dgi_compliance.fiscal.router import CHANNEL_MCF

DOCTYPE = "DGI Fiscal Operation"
WAITING = ("Queued", "Leased", "Uncertain", "Checking")
ACTIVE = WAITING + ("Dispute", "Pending")      # Pending: e-MCF request not confirmed (journal unique)
REALTIME_EVENT = "dgi_fiscal_operation"
WAKE_EVENT = "dgi_mcf_wake"          # wakes the browser station of a device (no polling needed)


def _settings():
    from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings
    return get_settings()


def lease_seconds():
    return max(60, cint(_settings().get("mcf_lease_seconds") or 180))


def active_operation(invoice: str, statuses=ACTIVE):
    rows = frappe.get_all(DOCTYPE, filters={"sales_invoice": invoice, "status": ["in", list(statuses)]},
                          fields=["name", "status", "payload_hash", "channel"], order_by="creation desc", limit=1)
    return rows[0] if rows else None


def _publish(op):
    try:
        frappe.publish_realtime(REALTIME_EVENT, {"operation": op.name, "status": op.status,
                                                 "invoice": op.sales_invoice, "error": op.get("error_message")},
                                doctype="Sales Invoice", docname=op.sales_invoice, after_commit=True)
    except Exception:
        pass


def wake_station(device: str):
    """Tell the Poste MCF page(s) that work is waiting for this MCF. Only the device name is sent."""
    try:
        frappe.publish_realtime(WAKE_EVENT, {"device": device}, after_commit=True)
    except Exception:
        pass


def queue_position(op) -> int:
    return frappe.db.count(DOCTYPE, {"mcf_device": op.mcf_device, "status": ["in", list(WAITING)],
                                     "creation": ["<=", op.creation]})


# --------------------------------------------------------------------------- creation
def request_mcf_normalization(doc, settings, route, fallback_reason=None) -> dict:
    """Create (or return) the operation of this invoice. Committed by the caller."""
    from dgi_compliance.edef.audit import log_exchange
    from dgi_compliance.edef.tasks import payload_hash
    from dgi_compliance.fiscal.mcf_payload import build_mcf_invoice

    if not doc.is_new():
        frappe.db.sql("select name from `tabSales Invoice` where name=%s for update", doc.name)
    existing = active_operation(doc.name)
    if existing and (existing.channel or CHANNEL_MCF) != CHANNEL_MCF:
        return {"ok": False, "stage": "operation-active", "operation": existing.name, "status": existing.status,
                "message": _("Tentative {0} en cours ({1}, {2}) : pas d'envoi au MCF.").format(
                    existing.channel, existing.name, existing.status)}
    if existing:
        op = frappe.get_doc(DOCTYPE, existing.name)
        return {"ok": True, "stage": "queued-mcf", "operation": op.name, "status": op.status,
                "position": queue_position(op)}
    if route.point_of_sale and not frappe.has_permission("DGI Point of Sale", "read", route.point_of_sale):
        frappe.throw(_("Vous n'etes pas autorise a facturer sur le point de vente fiscal {0}.")
                     .format(route.point_of_sale), frappe.PermissionError)
    device = frappe.get_doc("DGI MCF Device", route.device)
    if device.status != "Actif":
        frappe.throw(_("Le MCF {0} n'est pas actif (statut {1}).").format(device.name, device.status))
    if device.point_of_sale != route.point_of_sale:
        frappe.throw(_("Le MCF {0} n'est pas rattache au point de vente {1}.").format(device.name, route.point_of_sale))
    invoice, dto = build_mcf_invoice(doc, settings, route)
    from dgi_compliance.fiscal.router import decision_fields
    last = frappe.get_all(DOCTYPE, filters={"sales_invoice": doc.name}, fields=["name", "attempt"],
                          order_by="creation desc", limit=1)
    op = frappe.get_doc({
        **decision_fields(route),
        "previous_operation": last[0].name if last else None,
        "doctype": DOCTYPE, "operation_id": str(uuid.uuid4()), "op_type": "NORMALIZE", "status": "Queued",
        "channel": CHANNEL_MCF, "sales_invoice": doc.name, "company": doc.company,
        "point_of_sale": route.point_of_sale, "mcf_device": device.name, "fn": invoice["fn"],
        "attempt": (cint(last[0].attempt) + 1) if last else 1, "queued_at": now_datetime(),
        "requested_by": frappe.session.user,
        "payload": json.dumps(invoice, ensure_ascii=False, indent=1, default=str),
        "payload_hash": payload_hash(dto),
        "fallback": 1 if fallback_reason else 0, "fallback_reason": (fallback_reason or "")[:500] or None,
    }).insert(ignore_permissions=True)
    wake_station(device.name)
    doc.db_set({"custom_dgi_status": "Pending", "custom_dgi_error": None, "custom_dgi_channel": CHANNEL_MCF,
                "custom_dgi_operation": op.name, "dgi_point_of_sale": route.point_of_sale}, update_modified=False)
    log_exchange("mcf-queue", {"operation": op.name, "device": device.name, "mv": invoice["totals"]["mv"],
                               "mt": invoice["totals"]["mt"], "fallback": fallback_reason or None},
                 None, reference_invoice=doc.name)
    _publish(op)
    return {"ok": True, "stage": "queued-mcf", "operation": op.name, "status": op.status,
            "position": queue_position(op)}


def _requeue(op, reason):
    max_attempts = max(1, cint(_settings().get("mcf_max_attempts") or 3))
    if cint(op.attempt) >= max_attempts:
        return None
    new = frappe.get_doc({
        "doctype": DOCTYPE, "operation_id": str(uuid.uuid4()), "op_type": op.op_type, "status": "Queued",
        "channel": op.channel, "sales_invoice": op.sales_invoice, "company": op.company,
        "point_of_sale": op.point_of_sale, "mcf_device": op.mcf_device, "fn": op.fn,
        "attempt": cint(op.attempt) + 1, "queued_at": now_datetime(), "requested_by": op.requested_by,
        "payload": op.payload, "payload_hash": op.payload_hash, "previous_operation": op.name,
        "error_message": reason[:500] if reason else None,
        "fallback": op.get("fallback") or 0, "fallback_reason": op.get("fallback_reason"),
    }).insert(ignore_permissions=True)
    wake_station(op.mcf_device)
    if op.sales_invoice:
        frappe.db.set_value("Sales Invoice", op.sales_invoice,
                            {"custom_dgi_operation": new.name, "custom_dgi_status": "Pending", "custom_dgi_error": None},
                            update_modified=False)
    return new


def cancel_queued_for_invoice(invoice: str, reason: str) -> int:
    n = 0
    for name in frappe.get_all(DOCTYPE, filters={"sales_invoice": invoice, "status": "Queued"}, pluck="name"):
        op = frappe.get_doc(DOCTYPE, name)
        op.db_set({"status": "Cancelled", "finished_at": now_datetime(), "error_message": reason[:500]})
        _publish(op)
        n += 1
    return n


# --------------------------------------------------------------------------- lease
def expire_leases(device=None) -> int:
    filters = {"status": ["in", ["Leased", "Checking"]], "lease_expires_at": ["<", now_datetime()]}
    if device:
        filters["mcf_device"] = device
    n = 0
    for name in frappe.get_all(DOCTYPE, filters=filters, pluck="name"):
        op = frappe.get_doc(DOCTYPE, name)
        op.db_set({"status": "Uncertain", "error_code": "BAIL_EXPIRE",
                   "error_message": _("Bail expire sans resultat : rapprochement C1h necessaire avant toute "
                                      "nouvelle operation sur ce MCF.")})
        from dgi_compliance.gateway.events import log_event
        log_event(op.gateway, "OPERATION_INCERTAINE", f"{op.name} ({op.sales_invoice}) : bail expire.",
                  level="Alerte", point_of_sale=op.point_of_sale, device=op.mcf_device)
        _publish(op)
        n += 1
    return n


def _expected_nif(op):
    try:
        return (json.loads(op.payload or "{}") or {}).get("nif") or ""
    except ValueError:
        return ""


def _lease_message(op, device, kind):
    payload = json.loads(op.payload or "{}") if op.payload else {}
    msg = {"operation_id": op.operation_id, "op_type": kind, "lease_token": op.lease_token,
           "lease_expires_in": lease_seconds(),
           "device": {"nid": device.nid, "nif": _expected_nif(op) or device.nif}}
    if kind in ("NORMALIZE", "CHECK"):
        msg["invoice"] = payload
        msg["fn"] = op.fn
    return msg


def lease_for_gateway(gw) -> dict:
    device_name = gw.mcf_device
    if not device_name:
        return {"idle": True, "reason": "aucun MCF rattache"}
    frappe.db.sql("select name from `tabDGI MCF Device` where name=%s for update", device_name)
    device = frappe.get_doc("DGI MCF Device", device_name)
    expire_leases(device_name)
    idle = {"idle": True, "poll_interval_s": flt(_settings().get("gateway_poll_interval_s") or 2.0)}
    if device.status != "Actif":
        return dict(idle, reason=f"MCF {device.status}")

    held = frappe.get_all(DOCTYPE, filters={"mcf_device": device_name, "status": ["in", ["Leased", "Checking"]]},
                          fields=["name", "gateway", "status"], order_by="creation asc", limit=1)
    if held:
        op = frappe.get_doc(DOCTYPE, held[0].name)
        if op.gateway != gw.name:
            return dict(idle, reason="operation tenue par une autre passerelle")
        op.db_set("lease_expires_at", add_to_date(now_datetime(), seconds=lease_seconds()))
        return _lease_message(op, device, "CHECK" if op.status == "Checking" else op.op_type)

    if frappe.db.exists(DOCTYPE, {"mcf_device": device_name, "status": "Dispute"}):
        return dict(idle, reason="litige ouvert sur ce MCF : decision du DGI Fiscal Manager requise")

    unc = frappe.get_all(DOCTYPE, filters={"mcf_device": device_name, "status": "Uncertain"},
                         pluck="name", order_by="creation asc", limit=1)
    if unc:
        op = frappe.get_doc(DOCTYPE, unc[0])
        op.db_set({"status": "Checking", "gateway": gw.name, "lease_token": secrets.token_hex(16),
                   "station_user": getattr(frappe.local, "dgi_station_user", None),
                   "leased_at": now_datetime(), "check_count": cint(op.check_count) + 1,
                   "lease_expires_at": add_to_date(now_datetime(), seconds=lease_seconds())})
        _publish(op)
        return _lease_message(op, device, "CHECK")

    queued = frappe.get_all(DOCTYPE, filters={"mcf_device": device_name, "status": "Queued"},
                            pluck="name", order_by="creation asc", limit=1)
    if not queued:
        return idle
    op = frappe.get_doc(DOCTYPE, queued[0])
    op.db_set({"status": "Leased", "gateway": gw.name, "lease_token": secrets.token_hex(16),
               "station_user": getattr(frappe.local, "dgi_station_user", None),
               "leased_at": now_datetime(), "lease_expires_at": add_to_date(now_datetime(), seconds=lease_seconds())})
    _publish(op)
    return _lease_message(op, device, op.op_type)


# --------------------------------------------------------------------------- results
def validate_security_data(op, data: dict, device) -> list[str]:
    errors = []
    mid = (data.get("mid") or "").strip()
    if F.nid_of(mid) != device.nid:
        errors.append(_("MID {0} : NID different du MCF attendu {1}").format(mid or "?", device.nid))
    exp_nif = _expected_nif(op)
    if exp_nif and (data.get("nif") or "") != exp_nif:
        errors.append(_("NIF {0} different du NIF de la facture {1}").format(data.get("nif"), exp_nif))
    if (data.get("fn") or "") != (op.fn or ""):
        errors.append(_("numero de facture {0} different de {1}").format(data.get("fn"), op.fn))
    sig = (data.get("sig") or "").replace("-", "").upper()
    if not F.SIG_RE.match(sig):
        errors.append(_("Code DEF/DGI {0} : 24 caracteres alphanumeriques attendus").format(data.get("sig")))
    if not F.DT_RE.match(str(data.get("dt") or "")):
        errors.append(_("date de normalisation illisible ({0})").format(data.get("dt")))
    if data.get("ft") and data["ft"] not in F.INVOICE_TYPES:
        errors.append(_("type de facture {0} inconnu").format(data.get("ft")))
    return errors


def counter_problem(data, device) -> str | None:
    """A normalized result must carry a TC (total counter of the MCF) greater than the last one
    accepted for this MCF: one operation at a time per MCF (barrier), so TC only grows. An equal or
    smaller TC is a replayed or forged result."""
    tc, last = cint(data.get("tc")), cint(device.last_tc)
    if last and tc and tc <= last:
        return _("compteur total {0} non croissant (dernier accepte {1})").format(tc, last)
    return None


def _check_counter(op, data, device):
    """TC must grow by one per invoice of this MCF: a jump reveals an invoice made outside the SFE."""
    try:
        tc = cint(data.get("tc"))
    except Exception:
        return
    last = cint(device.last_tc)
    if last and tc and tc > last + 1 and not data.get("recovered"):
        from dgi_compliance.gateway.events import log_event
        log_event(op.gateway, "COMPTEUR_DISCONTINU",
                  _("Compteur total du MCF {0} : {1} apres {2}. Une ou plusieurs factures ont ete enregistrees "
                    "sur ce MCF en dehors d'ERPNext.").format(device.name, tc, last),
                  level="Critique", point_of_sale=op.point_of_sale, device=device.name)
    if tc > last:
        device.db_set("last_tc", tc, update_modified=False)


def apply_to_invoice(op, data):
    from dgi_compliance.edef.audit import log_exchange
    from dgi_compliance.edef.qr import make_qr_data_uri
    sig = (data.get("sig") or "").replace("-", "").upper()
    qr = F.qr_content(data.get("mid"), sig, data.get("nif"), data.get("dt"))
    payload = json.loads(op.payload or "{}")
    operator = payload.get("operator") or {}
    frappe.db.set_value("Sales Invoice", op.sales_invoice, {
        "custom_dgi_status": "Normalized",
        "custom_dgi_code_def": F.format_sig(sig),
        "custom_dgi_counters": "{0}/{1} {2}".format(data.get("fc") or "?", data.get("tc") or "?", data.get("ft") or ""),
        "custom_dgi_nim": data.get("mid"),
        "custom_dgi_datetime": F.display_datetime(data.get("dt")),
        "custom_dgi_qr_code": qr,
        "custom_dgi_qr_image": make_qr_data_uri(qr),
        "custom_dgi_error": None,
        "custom_dgi_normalized_on": now_datetime(),
        "custom_dgi_operator": operator.get("id"),
        "custom_dgi_operator_name": operator.get("name"),
        "custom_dgi_payload_hash": op.payload_hash,
        "custom_dgi_channel": CHANNEL_MCF,
        "custom_dgi_operation": op.name,
        "custom_dgi_response": json.dumps({"channel": CHANNEL_MCF, "operation": op.name, "request": payload,
                                           "result": data}, ensure_ascii=False, default=str),
    }, update_modified=False)
    log_exchange("mcf-normalize", {"operation": op.name, "gateway": op.gateway}, data,
                 reference_invoice=op.sales_invoice)
    # v5.4.0: sequential reference of the point de vente, given as soon as the result is committed
    from dgi_compliance.fiscal.sequence import assign_when_committed
    assign_when_committed(op.sales_invoice, source="MCF", operation=op.name)


def _invoice_error(op, message):
    if op.sales_invoice:
        frappe.db.set_value("Sales Invoice", op.sales_invoice,
                            {"custom_dgi_status": "Error", "custom_dgi_error": (message or "Echec MCF")[:140]},
                            update_modified=False)


def apply_result(gw, result: dict) -> dict:
    from dgi_compliance.gateway.events import log_event
    if not isinstance(result, dict) or not result.get("operation_id"):
        frappe.throw(_("Resultat sans identifiant d'operation."))
    name = frappe.db.get_value(DOCTYPE, {"operation_id": result["operation_id"]}, "name")
    if not name:
        raise frappe.DoesNotExistError(_("Operation inconnue."))
    frappe.db.sql(f"select name from `tab{DOCTYPE}` where name=%s for update", name)
    op = frappe.get_doc(DOCTYPE, name)
    if op.mcf_device != gw.mcf_device:
        log_event(gw.name, "OPERATION_ETRANGERE", _("Resultat pour une operation d'un autre MCF ({0}).").format(op.name),
                  level="Critique")
        frappe.throw(_("Cette operation ne concerne pas le MCF de la passerelle."), frappe.PermissionError)
    if op.op_type != "RESYNC" and op.status not in ("Normalized", "Rejected", "Cancelled", "Dispute") \
            and op.gateway != gw.name:
        # an operation that this gateway never leased (Queued, or leased by another gateway)
        log_event(gw.name, "RESULTAT_SANS_BAIL",
                  _("Resultat {0} pour {1} ({2}) qui n'a pas ete confiee a cette passerelle.")
                  .format(result.get("outcome"), op.name, op.status), level="Critique",
                  point_of_sale=op.point_of_sale, device=op.mcf_device)
        frappe.throw(_("Operation non confiee a cette passerelle."), frappe.PermissionError)
    device = frappe.get_doc("DGI MCF Device", op.mcf_device)
    outcome = result.get("outcome")
    data = result.get("data") or {}
    err = result.get("error") or {}
    token_ok = bool(result.get("lease_token")) and result.get("lease_token") == op.lease_token
    stored = json.dumps(result, ensure_ascii=False, default=str)[:60000]

    if op.op_type == "RESYNC":
        op.db_set({"status": "Done", "finished_at": now_datetime(), "result": stored,
                   "error_message": err.get("message")})
        return {"ok": True, "status": op.status}

    if outcome == "normalized":
        sig = (data.get("sig") or "").replace("-", "").upper()
        if op.status == "Normalized":
            prev = json.loads(op.result or "{}").get("data", {}) if op.result else {}
            if (prev.get("sig") or "").replace("-", "").upper() == sig:
                return {"ok": True, "status": op.status, "duplicate": True}
        errors = validate_security_data(op, data, device)
        problem = counter_problem(data, device)
        if problem:
            errors.append(problem)
        if errors or op.status in ("Normalized", "Rejected", "Cancelled"):
            msg = "; ".join(errors) or _("resultat positif pour une operation deja close ({0})").format(op.status)
            op.db_set({"status": "Dispute", "result": stored, "error_code": "RESULTAT_CONTESTE", "error_message": msg[:500]})
            _invoice_error(op, _("Litige MCF : ") + msg)
            log_event(gw.name, "RESULTAT_CONTESTE", f"{op.name} : {msg}", level="Critique")
            _publish(op)
            return {"ok": False, "status": op.status, "errors": errors}
        apply_to_invoice(op, data)
        _check_counter(op, data, device)
        op.db_set({"status": "Normalized", "finished_at": now_datetime(), "result": stored,
                   "error_code": None, "error_message": None})
        _publish(op)
        return {"ok": True, "status": op.status}

    if outcome == "rejected":
        if op.status not in ("Leased", "Checking", "Uncertain") or not token_ok:
            return {"ok": True, "status": op.status, "ignored": _("rejet hors bail : ignore")}
        message = err.get("message") or _("Echec MCF")
        op.db_set({"status": "Rejected", "finished_at": now_datetime(), "result": stored,
                   "error_code": err.get("code"), "error_message": message[:500]})
        new = None
        if result.get("proven_not_normalized") and err.get("retryable"):
            new = _requeue(op, message)
        if not new:
            _invoice_error(op, message)
        _publish(op)
        return {"ok": True, "status": op.status, "requeued": new.name if new else None}

    if outcome == "uncertain":
        if op.status in ("Leased", "Checking") and token_ok:
            op.db_set({"status": "Uncertain", "result": stored, "error_code": err.get("code") or "INCERTAIN",
                       "error_message": (err.get("message") or "")[:500]})
            log_event(gw.name, "OPERATION_INCERTAINE", f"{op.name} : {err.get('message') or ''}", level="Alerte")
            _publish(op)
        return {"ok": True, "status": op.status}

    frappe.throw(_("Issue de resultat inconnue : {0}").format(outcome))


# --------------------------------------------------------------------------- heartbeat
def record_heartbeat(gw, snapshot: dict) -> dict:
    from dgi_compliance.gateway.events import log_event
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    now = now_datetime()
    gw.db_set({"last_seen": now, "last_ip": getattr(frappe.local, "request_ip", None),
               "gateway_version": (snapshot.get("gateway_version") or "")[:20],
               "host_info": json.dumps(snapshot.get("host") or {}, ensure_ascii=False)[:1000],
               "offline_alert": 0}, update_modified=False)
    if gw.mcf_device:
        device = frappe.get_doc("DGI MCF Device", gw.mcf_device)
        values = {"last_status": json.dumps(snapshot, ensure_ascii=False, default=str)[:20000],
                  "last_status_on": now}
        wrong_device = False
        if snapshot.get("mid"):
            values["last_mid"] = snapshot["mid"]
            if F.nid_of(snapshot["mid"]) != device.nid:
                wrong_device = True
                log_event(gw.name, "NID_INATTENDU",
                          _("La passerelle voit le MCF {0} au lieu de {1}.").format(snapshot["mid"], device.nid),
                          level="Critique")
        if snapshot.get("dgi_last_sync"):
            last = F.parse_device_datetime(snapshot["dgi_last_sync"])
            if last:
                values["dgi_last_sync"] = last
        if snapshot.get("dgi_pending") not in (None, ""):
            # C2h: EC = transactions sent, DC = transactions available in the MCF -> not sent = DC - EC
            # (v5.3.2; older gateways send only DC, kept as before)
            available = cint(snapshot.get("dgi_pending"))
            if snapshot.get("dgi_sent") not in (None, ""):
                available = max(0, available - cint(snapshot.get("dgi_sent")))
            values["dgi_pending"] = available
        if snapshot.get("flags") is not None:
            values["status_flags"] = ", ".join(snapshot.get("flags") or [])
        if isinstance(snapshot.get("tax_rates"), dict) and snapshot["tax_rates"]:
            values["tax_rates"] = json.dumps(snapshot["tax_rates"], sort_keys=True)
            if values["tax_rates"] != (device.get("tax_rates") or ""):
                _compare_rates(gw, device, snapshot["tax_rates"])
        if snapshot.get("last_invoice"):
            values["last_invoice"] = str(snapshot["last_invoice"])[:140]
        device.db_set(values, update_modified=False)
        if not wrong_device:
            check_identity(device, mid=snapshot.get("mid"), nif=snapshot.get("nif"), gateway=gw.name)
            _audit_counters(gw, device, snapshot)
        frappe.db.set_value(DOCTYPE, {"mcf_device": device.name, "gateway": gw.name,
                                      "status": ["in", ["Leased", "Checking"]]},
                            "lease_expires_at", add_to_date(now, seconds=lease_seconds()))
    return {"server_time": str(now), "poll_interval_s": flt(_settings().get("gateway_poll_interval_s") or 2.0)}


def check_identity(device, mid=None, nif=None, gateway=None) -> str | None:
    """MID and NIF read in the MCF versus the values accepted at validation (livrable B section 7).
    A difference sets the MCF to 'A revalider': no invoice goes to it until a responsible validates it
    again (the fallback policy of the point de vente applies meanwhile). Returns the event code or None."""
    from dgi_compliance.gateway.events import log_event
    if device.get("status") != "Actif":
        return None
    code, message = None, None
    validated = (device.get("validated_mid") or "").strip()
    if mid and validated and str(mid).strip() != validated:
        code = "MID_CHANGE"
        message = _("Le MCF {0} annonce le MID {1} au lieu du MID valide {2} (compteur d'activation change) : "
                    "le MCF ne recoit plus de factures jusqu'a une nouvelle validation.").format(device.name, mid, validated)
    expected = ""
    if nif and device.get("point_of_sale"):
        company = frappe.db.get_value("DGI Point of Sale", device.point_of_sale, "company")
        expected = (frappe.db.get_value("Company", company, "tax_id") or "").strip() if company else ""
    if not code and nif and expected and str(nif).strip().upper() != expected.upper():
        code = "NIF_MCF_DIFFERENT"
        message = _("Le MCF {0} porte le NIF {1}, la societe du point de vente a le NIF {2} : le MCF ne recoit plus "
                    "de factures jusqu'a une nouvelle validation.").format(device.name, nif, expected)
    if not code:
        return None
    frappe.db.set_value("DGI MCF Device", device.name, {"status": "A revalider", "nif_check": "Different"
                        if code == "NIF_MCF_DIFFERENT" else device.get("nif_check")}, update_modified=False)
    log_event(gateway, code, message, level="Critique", point_of_sale=device.get("point_of_sale"), device=device.name,
              details={"mid": mid, "nif": nif, "validated_mid": validated, "expected_nif": expected})
    return code


def _compare_rates(gw, device, rates: dict):
    """Rates announced by the MCF (C1h) versus the DGI Tax Group catalogue of ERPNext."""
    from dgi_compliance.gateway.events import log_event
    diffs = []
    for letter, rate in sorted(rates.items()):
        ours = frappe.db.get_value("DGI Tax Group", letter, "rate")
        if ours is not None and abs(flt(ours) - flt(rate)) > 0.001:
            diffs.append(f"{letter}: MCF {flt(rate):g} % / ERPNext {flt(ours):g} %")
    if diffs:
        log_event(gw.name, "TAUX_MCF_DIFFERENTS", _("Taux du MCF {0} differents d'ERPNext : {1}. Synchronisez les "
                                                    "groupes de taxation (e-MCF) ou mettez a jour le MCF.")
                  .format(device.name, "; ".join(diffs)), level="Alerte", device=device.name)


def _audit_counters(gw, device, snapshot):
    """Security control: the counters of the MCF must match what ERPNext accepted.
    * MCF total counter LOWER than the last one accepted: ERPNext holds security elements the MCF
      never produced (forged results, or another MCF) -> Critique;
    * HIGHER while nothing is in progress: invoices were normalized on this MCF outside ERPNext ->
      Alerte (the next normalized result re-synchronizes the counter);
    * last invoice of the MCF known in ERPNext with another code DEF -> Critique.
    One alert per counter value (cache) to avoid repeating it at every heartbeat."""
    from dgi_compliance.gateway.events import log_event
    tc = cint(snapshot.get("tc")) if str(snapshot.get("tc") or "").strip().isdigit() else None
    if tc is None:
        return
    if frappe.db.exists(DOCTYPE, {"mcf_device": device.name, "status": ["in", ["Leased", "Checking", "Uncertain"]]}):
        return
    last = cint(device.last_tc)
    if not last:
        device.db_set("last_tc", tc, update_modified=False)
        return
    key = frappe.cache.make_key(f"dgi_tc_audit|{device.name}", shared=True)
    seen = frappe.cache.get(key)
    marker = f"{tc}|{last}"
    if (seen.decode() if isinstance(seen, bytes) else seen) == marker:
        return
    if tc < last:
        log_event(gw.name, "COMPTEUR_INCOHERENT",
                  _("Le MCF {0} annonce {1} transactions, ERPNext en a accepte {2} : des elements de securite "
                    "enregistres dans ERPNext ne viennent pas de ce MCF. Verifiez les dernieres factures sur le "
                    "portail DGI.").format(device.name, tc, last), level="Critique", device=device.name)
    elif tc > last:
        log_event(gw.name, "FACTURES_HORS_ERPNEXT",
                  _("Le MCF {0} annonce {1} transactions, ERPNext en connait {2} : {3} normalisation(s) faite(s) "
                    "sur ce MCF en dehors d'ERPNext.").format(device.name, tc, last, tc - last),
                  level="Alerte", device=device.name)
    fn, sig = str(snapshot.get("last_invoice") or ""), str(snapshot.get("last_sig") or "").replace("-", "").upper()
    if fn and sig and frappe.db.exists("Sales Invoice", fn):
        ours = (frappe.db.get_value("Sales Invoice", fn, "custom_dgi_code_def") or "").replace("-", "").upper()
        if ours and ours != sig:
            log_event(gw.name, "CODE_DEF_DIFFERENT",
                      _("Facture {0} : code DEF du MCF {1}, code DEF enregistre dans ERPNext {2}.")
                      .format(fn, sig, ours), level="Critique", device=device.name)
    frappe.cache.set(key, marker, ex=7 * 86400)


# --------------------------------------------------------------------------- scheduler
def scheduled_maintenance():
    """Every minute: expired leases, silent gateways, operations waiting too long."""
    from dgi_compliance.gateway.events import log_event
    expire_leases()
    settings = _settings()
    minutes = max(1, cint(settings.get("gateway_offline_minutes") or 2))
    limit = add_to_date(now_datetime(), minutes=-minutes)
    for gw in frappe.get_all("DGI Gateway", filters={"status": "Active", "offline_alert": 0},
                             fields=["name", "last_seen", "point_of_sale", "mcf_device", "kind"]):
        if gw.last_seen and get_datetime(gw.last_seen) >= limit:
            continue
        # a browser station is only open while the cash desk works: silence is normal unless invoices wait
        waiting = frappe.db.get_value(DOCTYPE, {"mcf_device": gw.mcf_device, "status": ["in", list(WAITING)],
                                                "queued_at": ["<", limit]}, "name")
        channel = frappe.db.get_value("DGI Point of Sale", gw.point_of_sale, "active_channel")
        if not waiting and not (channel == CHANNEL_MCF and gw.kind != "Poste navigateur"):
            continue
        frappe.db.set_value("DGI Gateway", gw.name, "offline_alert", 1, update_modified=False)
        if gw.kind == "Poste navigateur":
            log_event(gw.name, "POSTE_MCF_FERME",
                      _("Des factures attendent le MCF de {0} depuis plus de {1} min : ouvrez la page Poste MCF sur "
                        "la caisse ou le MCF est branche.").format(gw.point_of_sale, minutes), level="Critique")
        else:
            log_event(gw.name, "PASSERELLE_MUETTE",
                      _("Aucun signal de la passerelle {0} depuis plus de {1} min.").format(gw.name, minutes),
                      level="Critique")
    frappe.db.commit()
    try:
        from dgi_compliance.fiscal.fallback import probe_all
        probe_all()
    except Exception:
        frappe.log_error(title="DGI : test de retour du canal prioritaire")
    frappe.db.commit()
    try:
        from dgi_compliance.fiscal.fallback import send_stale_mcf_queue_to_emcf
        send_stale_mcf_queue_to_emcf()
    except Exception:
        frappe.log_error(title="DGI : secours e-MCF des factures en file au MCF")
    frappe.db.commit()


def scheduled_sync_age():
    """Hourly: last MCF -> DGI synchronization (protocol C2h: alert after 1 day; SFE 6.1: 7 days)."""
    from dgi_compliance.gateway.events import log_event
    settings = _settings()
    warn = max(1, cint(settings.get("mcf_sync_warning_days") or 1))
    crit = max(warn, cint(settings.get("mcf_sync_critical_days") or 7))
    now = now_datetime()
    for d in frappe.get_all("DGI MCF Device", filters={"status": "Actif"},
                            fields=["name", "dgi_last_sync", "sync_alert"]):
        if not d.dgi_last_sync:
            continue
        age = (now - get_datetime(d.dgi_last_sync)).total_seconds() / 86400.0
        level = "Critique" if age > crit else ("Alerte" if age > warn else "OK")
        if level != (d.sync_alert or "OK"):
            frappe.db.set_value("DGI MCF Device", d.name, "sync_alert", level, update_modified=False)
            if level != "OK":
                log_event(None, "SYNCHRO_DGI", _("Le MCF {0} n'a pas joint la DGI depuis {1:.1f} jour(s).")
                          .format(d.name, age), level=level, device=d.name,
                          point_of_sale=frappe.db.get_value("DGI MCF Device", d.name, "point_of_sale"))
    frappe.db.commit()


def daily_sync_reminder():
    """SFE requirement 6.1: inform the users DAILY while an MCF has not reached the DGI for more
    than the critical delay."""
    from dgi_compliance.gateway.events import notify
    for d in frappe.get_all("DGI MCF Device", filters={"status": "Actif", "sync_alert": "Critique"},
                            fields=["name", "dgi_last_sync", "point_of_sale"]):
        notify(f"[DGI] MCF {d.name} non synchronise avec la DGI",
               _("Le MCF {0} ({1}) n'a pas joint la DGI depuis le {2}. Relancez la synchronisation (DGI Point of Sale "
                 "> MCF > Relancer la synchronisation) et verifiez la carte SIM du MCF.")
               .format(d.name, d.point_of_sale, d.dgi_last_sync))
