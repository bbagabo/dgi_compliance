"""
Invoice certification flow:

  ERPNext Sales Invoice  ──►  POST /api/invoice  ──►  DGI returns
       on_submit hook          (JSON body)            { uid, defDgiCode,
                                                         counters, time,
                                                         qrCode, status }

  The result is written back to the Sales Invoice via Custom Fields
  (dgi_uid, dgi_code_def, dgi_counters, dgi_qr_code, dgi_status) *and*
  mirrored into the DGI Pending Invoice doctype, which becomes our retry
  queue when the call failed or only returned a 'Pending' status.

  We NEVER overwrite the Sales Invoice itself if certification fails —
  the invoice keeps its submitted state and the user can re-trigger
  certification from the Pending Invoice list.
"""

from __future__ import annotations

import uuid

import frappe
from frappe import _

from dgi_compliance.api import client
from dgi_compliance.utils.json_builder import build_invoice_request_body
from dgi_compliance.utils.mapping import resolve_pos_for_invoice


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

@frappe.whitelist()
def certify_sales_invoice(sales_invoice: str, force: bool = False) -> dict:
    """Send a Sales Invoice to DGI for certification.

    Called automatically from the on_submit hook, and manually from the
    Desk via the "Send to DGI" form button.
    """
    si = frappe.get_doc("Sales Invoice", sales_invoice)

    # Re-certification guard.
    if si.get("dgi_code_def") and not force:
        return {"status": "already_certified",
                "dgi_code_def": si.dgi_code_def,
                "dgi_qr_code": si.dgi_qr_code}

    pos_code = resolve_pos_for_invoice(si)
    if not pos_code:
        frappe.throw(_("No DGI POS resolvable for Sales Invoice {0}.").format(si.name))

    # Stable UID per submission — used for idempotent retries.
    if not si.get("dgi_uid"):
        uid = str(uuid.uuid4())
        frappe.db.set_value("Sales Invoice", si.name, "dgi_uid", uid,
                            update_modified=False)
        si.dgi_uid = uid

    body = build_invoice_request_body(si, pos_code)
    pending = _upsert_pending(si, pos_code)

    try:
        resp = client.post(
            client.BILLING_PATH, body,
            pos_code=pos_code,
            action="Send Invoice",
            document_type="Sales Invoice",
            document_no=si.name,
            entry_type="Send Invoice",
        )
    except client.DGIAPIError as exc:
        _record_failure(si, pending, str(exc))
        # Do not re-raise; caller (on_submit hook) must not block submission.
        return {"status": "error", "message": str(exc)}

    _apply_response_to_invoice(si, resp)
    _apply_response_to_pending(pending, resp)

    return {"status": "ok",
            "dgi_code_def": si.dgi_code_def,
            "dgi_qr_code": si.dgi_qr_code,
            "dgi_status": si.dgi_status}


@frappe.whitelist()
def status_request(sales_invoice: str) -> dict:
    """GET /api/invoice — query DGI for the latest status of a UID."""
    si = frappe.get_doc("Sales Invoice", sales_invoice)
    if not si.get("dgi_uid"):
        frappe.throw(_("Sales Invoice {0} has no DGI UID yet.").format(si.name))
    pos_code = si.get("dgi_emcf_pos") or resolve_pos_for_invoice(si)
    resp = client.get(
        f"{client.BILLING_PATH}?uid={si.dgi_uid}",
        pos_code=pos_code,
        action="Status Request",
        document_type="Sales Invoice",
        document_no=si.name,
    )
    _apply_response_to_invoice(si, resp)
    return resp


@frappe.whitelist()
def cancel_certification(sales_invoice: str, reason: str = "") -> dict:
    """Tell DGI that a previously-certified invoice has been cancelled.

    The DGI spec expects a credit-note-like payload, not a true 'delete'.
    On ERPNext this happens via on_cancel of the original invoice OR via
    submission of a credit-note Sales Invoice (is_return = 1).
    """
    si = frappe.get_doc("Sales Invoice", sales_invoice)
    if not si.get("dgi_code_def"):
        return {"status": "nothing_to_cancel"}
    pos_code = si.get("dgi_emcf_pos") or resolve_pos_for_invoice(si)
    body = build_invoice_request_body(si, pos_code, cancel=True, reason=reason)
    return client.post(
        client.BILLING_PATH, body,
        pos_code=pos_code,
        action="Cancel Invoice",
        document_type="Sales Invoice",
        document_no=si.name,
        entry_type="Cancel Invoice",
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _upsert_pending(si, pos_code: str):
    """Find or create the DGI Pending Invoice for this Sales Invoice."""
    name = frappe.db.exists("DGI Pending Invoice",
                            {"document_type": "Sales Invoice",
                             "document_no": si.name})
    if name:
        pending = frappe.get_doc("DGI Pending Invoice", name)
    else:
        pending = frappe.new_doc("DGI Pending Invoice")
        pending.document_type = "Sales Invoice"
        pending.document_no = si.name
        pending.document_uid = si.get("dgi_uid")
        pending.pos_code = pos_code
        pending.document_status = "Pending"
        pending.retry_count = 0
        pending.insert(ignore_permissions=True)
    return pending


def _apply_response_to_invoice(si, resp: dict) -> None:
    """Mirror DGI response fields onto the Sales Invoice custom fields."""
    if not resp:
        return
    updates = {
        "dgi_code_def": resp.get("defDgiCode") or resp.get("dgi_code_def"),
        "dgi_counters": resp.get("counters") or resp.get("dgi_counters"),
        "dgi_qr_code": resp.get("qrCode") or resp.get("qr_code"),
        "dgi_status": _normalize_status(resp.get("status")),
    }
    updates = {k: v for k, v in updates.items() if v}
    if not updates:
        return
    for k, v in updates.items():
        frappe.db.set_value("Sales Invoice", si.name, k, v,
                            update_modified=False)
        setattr(si, k, v)


def _apply_response_to_pending(pending, resp: dict) -> None:
    pending.dgi_code = resp.get("defDgiCode") or resp.get("dgi_code_def")
    pending.def_counters = resp.get("counters") or resp.get("dgi_counters")
    pending.def_time = resp.get("time") or resp.get("dgi_time")
    pending.qr_code = resp.get("qrCode") or resp.get("qr_code")
    pending.document_status = _normalize_status(resp.get("status")) or "Sent"
    pending.last_attempt = frappe.utils.now_datetime()
    pending.last_error = ""
    pending.save(ignore_permissions=True)


def _record_failure(si, pending, message: str) -> None:
    pending.retry_count = (pending.retry_count or 0) + 1
    pending.last_attempt = frappe.utils.now_datetime()
    pending.last_error = message[:1000]
    pending.document_status = "Error"
    pending.save(ignore_permissions=True)
    frappe.db.set_value("Sales Invoice", si.name, "dgi_status", "Failed",
                        update_modified=False)


def _normalize_status(raw: str | None) -> str:
    """Map any DGI status string to our enum."""
    if not raw:
        return ""
    mapping = {
        "normalized": "Normalized",
        "issued": "Normalized",
        "ok": "Normalized",
        "success": "Normalized",
        "pending": "Pending",
        "cancelled": "Cancelled",
        "canceled": "Cancelled",
        "rejected": "Failed",
        "error": "Failed",
    }
    return mapping.get(str(raw).lower(), raw)
