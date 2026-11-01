"""HTTP API called by the site gateways (outbound HTTPS from the site, never the reverse).

All endpoints are POST. `enroll` is public (rate limited) and protected by a single-use code;
every other endpoint requires a signed request from an Active gateway (gateway.auth).
The whitelisting is `allow_guest` because the authentication is NOT a Frappe session: it is the
Ed25519 signature checked by `authenticate()` before anything else.
"""
import frappe
from frappe.rate_limiter import rate_limit

from dgi_compliance.gateway.auth import authenticate


def _done(value):
    frappe.db.commit()
    return value


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=10, seconds=3600)
def enroll(gateway_id=None, code=None, public_key=None, device=None, host=None):
    from dgi_compliance.dgi_compliance.doctype.dgi_gateway.dgi_gateway import enroll_gateway
    return _done(enroll_gateway(gateway_id, code, public_key, device or {}, host or {}))


@frappe.whitelist(allow_guest=True, methods=["POST"])
def config():
    gw = authenticate(require_active=False)
    from dgi_compliance.dgi_compliance.doctype.dgi_gateway.dgi_gateway import gateway_config
    return _done(gateway_config(gw))


@frappe.whitelist(allow_guest=True, methods=["POST"])
def heartbeat(snapshot=None):
    gw = authenticate()
    from dgi_compliance.fiscal.operations import record_heartbeat
    return _done(record_heartbeat(gw, snapshot or {}))


@frappe.whitelist(allow_guest=True, methods=["POST"])
def lease():
    gw = authenticate()
    from dgi_compliance.fiscal.operations import lease_for_gateway
    return _done(lease_for_gateway(gw))


@frappe.whitelist(allow_guest=True, methods=["POST"])
def result(result=None):
    gw = authenticate()
    from dgi_compliance.fiscal.operations import apply_result, lease_for_gateway
    answer = apply_result(gw, result or {})
    frappe.db.commit()
    nxt = lease_for_gateway(gw)
    if nxt.get("operation_id"):
        answer["next"] = nxt
    return _done(answer)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def event(code=None, message=None, details=None):
    gw = authenticate()
    from dgi_compliance.gateway.events import log_event
    level = "Critique" if (code or "").startswith(("NID_", "NIF_")) else "Alerte"
    log_event(gw.name, (code or "EVENEMENT")[:60], (message or "")[:1000], level=level, details=details or {})
    return _done({"ok": True})
