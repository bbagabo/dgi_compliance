"""
Scheduled job entry-points referenced from hooks.py.

Each function MUST be safe to run on an empty/uninitialised site (e.g. the
first deploy on Frappe Cloud) — they short-circuit gracefully when DGI
Settings are missing.
"""

from __future__ import annotations

import frappe

from dgi_compliance.api import invoice as invoice_api
from dgi_compliance.api import info as info_api
from dgi_compliance.api import client


def _is_configured() -> bool:
    if not frappe.db.exists("DocType", "DGI Settings"):
        return False
    s = frappe.get_single("DGI Settings")
    return bool(s.environment) and bool(
        s.production_url if s.environment == "Production" else s.test_url
    )


def retry_pending_invoices() -> None:
    """Re-send DGI Pending Invoice rows still in Pending or Error state."""
    if not _is_configured():
        return
    settings = frappe.get_single("DGI Settings")
    max_retries = int(settings.max_retry_count or 5)
    pending = frappe.get_all(
        "DGI Pending Invoice",
        filters=[
            ["document_status", "in", ["Pending", "Error"]],
            ["retry_count", "<", max_retries],
            ["document_type", "=", "Sales Invoice"],
        ],
        fields=["name", "document_no"],
        limit=50,
    )
    for row in pending:
        try:
            invoice_api.certify_sales_invoice(row.document_no, force=True)
        except Exception:
            frappe.log_error(
                title=f"DGI retry failed for {row.document_no}",
                message=frappe.get_traceback(),
            )


def refresh_reference_data() -> None:
    """Daily pull of every DGI dictionary."""
    if not _is_configured():
        return
    try:
        info_api.refresh_reference_data()
    except Exception:
        frappe.log_error(title="DGI daily reference refresh failed",
                         message=frappe.get_traceback())


def check_pos_token_validity() -> None:
    if not _is_configured():
        return
    try:
        info_api.check_pos_token_validity()
    except Exception:
        frappe.log_error(title="DGI POS token check failed",
                         message=frappe.get_traceback())


def ping_api_status() -> None:
    """Cheap 15-minute keep-alive that surfaces outages early."""
    if not _is_configured():
        return
    try:
        client.ping()
    except Exception:
        # The /status endpoint failing is itself useful data; the failure
        # already lands in DGI Invoice Log via the client.
        pass


def prune_old_logs() -> None:
    """Drop DGI Invoice Log rows older than the retention window (default 90 days)."""
    if not frappe.db.exists("DocType", "DGI Settings"):
        return
    settings = frappe.get_single("DGI Settings")
    days = int(settings.log_retention_days or 90)
    if days <= 0:
        return
    cutoff = frappe.utils.add_days(frappe.utils.nowdate(), -days)
    frappe.db.sql(
        "DELETE FROM `tabDGI Invoice Log` WHERE action_datetime < %s",
        cutoff,
    )
    frappe.db.commit()
