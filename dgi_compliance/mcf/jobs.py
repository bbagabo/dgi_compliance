"""DGI MCF Job queue (v4.0): invoice normalization through the local USB agent.

In 'MCF Physique (USB)' mode the server NEVER talks to the DGI: it only queues jobs.
The local agent pulls queued jobs (REST), drives the physical MCF over the serial/USB
protocol, and pushes back the security elements (38h response) which are written on
the Sales Invoice exactly like an e-DEF normalization result.
"""
import json
from datetime import datetime

import frappe
from frappe import _
from frappe.utils import flt, now_datetime
from dgi_compliance.edef.audit import log_exchange
from dgi_compliance.edef.qr import make_qr_data_uri
from dgi_compliance.mcf.payload import build_mcf_payload

ACTIVE_STATUSES = ("Queued", "In Progress")


def queue_mcf_job(doc, settings) -> dict:
    """Create (or reuse) the queue entry for this invoice. Raises on mapping errors so the
    user fixes the configuration BEFORE the job ever reaches the device."""
    existing = frappe.db.get_value(
        "DGI MCF Job", {"sales_invoice": doc.name, "status": ["in", ACTIVE_STATUSES]}, "name")
    if existing:
        frappe.msgprint(_("Un job MCF est deja en attente pour cette facture ({0}). "
                          "Verifiez que l'agent MCF local est demarre.").format(existing),
                        indicator="blue", title=_("MCF Physique"))
        return {"ok": True, "stage": "queued-mcf", "job": existing}

    payload = build_mcf_payload(doc, settings)
    job = frappe.get_doc({
        "doctype": "DGI MCF Job",
        "sales_invoice": doc.name,
        "status": "Queued",
        "queued_at": now_datetime(),
        "payload": json.dumps(payload, ensure_ascii=False, indent=1),
    }).insert(ignore_permissions=True)
    doc.db_set("custom_dgi_error", None, update_modified=False)
    log_exchange("mcf-queue",
                 {"invoice": doc.name, "job": job.name,
                  "mv": payload["totals"]["mv"], "mt": payload["totals"]["mt"]},
                 None, reference_invoice=doc.name)
    frappe.msgprint(_("Facture mise en file pour le MCF physique (job {0}). L'agent local la "
                      "normalisera des que le dispositif USB est disponible.").format(job.name),
                    indicator="blue", title=_("MCF Physique"))
    return {"ok": True, "stage": "queued-mcf", "job": job.name}


def cancel_pending_jobs(invoice: str):
    for name in frappe.get_all("DGI MCF Job",
                               filters={"sales_invoice": invoice,
                                        "status": ["in", ACTIVE_STATUSES]}, pluck="name"):
        frappe.db.set_value("DGI MCF Job", name,
                            {"status": "Cancelled", "finished_at": now_datetime()},
                            update_modified=False)


def apply_mcf_result(job, data: dict) -> dict:
    """Write the security elements returned by the MCF (38h response) on the Sales Invoice.
    QR content per protocol ch.5: RDCDEF01;{MID};{SIG};{NIF};{DT}."""
    doc = frappe.get_doc("Sales Invoice", job.sales_invoice)
    required = ("fc", "tc", "ft", "dt", "mid", "nif", "sig")
    missing = [k for k in required if not data.get(k)]
    if missing:
        frappe.throw(_("Resultat MCF incomplet: champs manquants {0}.").format(", ".join(missing)))
    if data.get("fn") and str(data["fn"]) != doc.name[:64]:
        frappe.throw(_("Resultat MCF: FN '{0}' ne correspond pas a la facture {1}.")
                     .format(data.get("fn"), doc.name))

    dtr = str(data["dt"]).strip()
    try:
        dt_fmt = datetime.strptime(dtr, "%Y%m%d%H%M%S").strftime("%d/%m/%Y %H:%M:%S")
    except ValueError:
        dt_fmt = dtr
    qr = "RDCDEF01;{mid};{sig};{nif};{dt}".format(
        mid=data["mid"], sig=data["sig"], nif=data["nif"], dt=dtr)

    doc.db_set({
        "custom_dgi_status": "Normalized",
        "custom_dgi_code_def": data["sig"],
        "custom_dgi_counters": "{0}/{1} {2}".format(data["fc"], data["tc"], data["ft"]),
        "custom_dgi_nim": data["mid"],
        "custom_dgi_datetime": dt_fmt,
        "custom_dgi_qr_code": qr,
        "custom_dgi_qr_image": make_qr_data_uri(qr),
        "custom_dgi_error": None,
    }, update_modified=False)

    # Reconciliation machine vs ERPNext (CDF) - log a warning line, never undo a fiscalized invoice.
    try:
        machine_mv = flt(data.get("machine_mv") or 0)
        erp_total = abs(flt(doc.get("base_grand_total") or 0))
        from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings
        tol = flt(get_settings().get("dgi_total_tolerance") or 500)
        if machine_mv and abs(machine_mv - erp_total) > tol:
            log_exchange("mcf-reconcile",
                         {"erp_total": erp_total, "machine_mv": machine_mv},
                         None, f"Ecart total MCF vs ERPNext > tolerance ({tol} CDF)",
                         reference_invoice=doc.name)
    except Exception:
        pass
    return {"invoice": doc.name, "code_def": data["sig"], "qr": qr}


def mark_job_error(job, msg: str):
    doc = frappe.get_doc("Sales Invoice", job.sales_invoice)
    doc.db_set("custom_dgi_status", "Error", update_modified=False)
    doc.db_set("custom_dgi_error", (msg or "Echec MCF")[:140], update_modified=False)
    log_exchange("mcf-error", {"job": job.name}, None, msg, reference_invoice=job.sales_invoice)
