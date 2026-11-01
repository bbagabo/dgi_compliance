"""Whitelisted REST endpoints for the local MCF agent (v4.0) - RETIRED in v5.1 (HL2).

The v4 agent is replaced by the site gateways (DGI Gateway, dgi_compliance.gateway.api):
heartbeat answers mode 'emcf' (the agent stays idle) and pull_jobs never hands out a job.
push_result is kept so that a job already in progress at upgrade time can still be settled.

Authentication: a dedicated ERPNext user holding the 'DGI MCF Agent' role, with an
API key/secret pair; the agent sends `Authorization: token <api_key>:<api_secret>`.

Mutual exclusion: every endpoint is inert unless DGI Compliance Settings ->
communication_mode == 'MCF Physique (USB)'. Conversely, while that mode is active the
e-DEF HTTP client refuses to send anything (see edef/client.py guard), so the two
channels can never run at the same time.
"""
import json

import frappe
from frappe import _
from frappe.utils import cint, now_datetime
from dgi_compliance.edef.audit import log_exchange
from dgi_compliance.mcf.jobs import apply_mcf_result, mark_job_error


def _guard():
    frappe.only_for(("System Manager", "DGI MCF Agent"))


@frappe.whitelist()
def heartbeat(agent_id=None, status=None):
    """Agent liveness + device diagnostics (C1h/C2h summaries). Always allowed so the
    Settings page can show the agent state in BOTH modes; returns the active mode so
    the agent stops driving the device when the site switches back to e-MCF."""
    _guard()
    txt = status if isinstance(status, str) else json.dumps(status or {}, ensure_ascii=False)
    frappe.db.set_value("DGI Compliance Settings", "DGI Compliance Settings", {
        "mcf_agent_last_seen": now_datetime(),
        "mcf_agent_status": ("[{0}] {1}".format(agent_id or "agent", txt or ""))[:1000],
    }, update_modified=False)
    return {"mode": "emcf", "port_hint": None, "message": "Agent v4 retire (v5.1)"}


@frappe.whitelist()
def pull_jobs(agent_id=None, limit=1):
    """Retired in v5.1: never hands out a job any more."""
    _guard()
    return {"mode": "emcf", "jobs": [], "message": "Agent v4 retire (v5.1) : installez la passerelle DGI Gateway."}


@frappe.whitelist()
def push_result(job, ok=None, result=None, error=None):
    """Receive the outcome of one job. Success -> the invoice gets its security elements
    (SIG/MID/compteurs/heure/QR) and becomes Normalized; failure -> invoice marked Error."""
    _guard()
    jdoc = frappe.get_doc("DGI MCF Job", job)
    if jdoc.status not in ("In Progress", "Queued"):
        frappe.throw(_("Job {0} n'est pas actif (statut {1}).").format(job, jdoc.status))
    data = result if isinstance(result, dict) else json.loads(result or "{}")
    ok = bool(cint(ok)) if not isinstance(ok, bool) else ok

    # v5.1: a job already in progress at upgrade time is still settled, whatever the default channel.

    if ok:
        applied = apply_mcf_result(jdoc, data)
        jdoc.db_set({"status": "Done", "error": None, "finished_at": now_datetime(),
                     "result": json.dumps(data, ensure_ascii=False, indent=1)},
                    update_modified=False)
        log_exchange("mcf-normalize", {"job": jdoc.name}, data,
                     reference_invoice=jdoc.sales_invoice)
        return {"ok": True, **applied}

    msg = (error or data.get("error") or "Echec MCF")
    jdoc.db_set({"status": "Error", "finished_at": now_datetime(),
                 "error": str(msg)[:500],
                 "result": json.dumps(data, ensure_ascii=False, indent=1) if data else None},
                update_modified=False)
    mark_job_error(jdoc, str(msg))
    return {"ok": False, "job": jdoc.name}


@frappe.whitelist()
def requeue_job(job):
    """Manual requeue from the desk (System Manager) after fixing the cause of an error."""
    frappe.only_for("System Manager")
    jdoc = frappe.get_doc("DGI MCF Job", job)
    if jdoc.status not in ("Error", "Cancelled"):
        frappe.throw(_("Seul un job en erreur/annule peut etre remis en file."))
    jdoc.db_set({"status": "Queued", "error": None, "finished_at": None},
                update_modified=False)
    return {"ok": True}
