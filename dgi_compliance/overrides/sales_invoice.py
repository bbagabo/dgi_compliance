"""
Sales Invoice & Customer hooks.

We do not subclass `erpnext.accounts.doctype.sales_invoice.sales_invoice.SalesInvoice`.
Instead, every behaviour ships as a small `doc_events` callback registered in
`hooks.py`. That keeps us 100% upgrade-safe: a future ERPNext release can
rename internals freely — only the public signature `(doc, method=None)`
needs to stay stable, which it does.
"""

from __future__ import annotations

import frappe
from frappe import _

from dgi_compliance.api import invoice as invoice_api
from dgi_compliance.utils import mapping


REQUIRED_INVOICE_FIELDS = ("dgi_invoice_type",)
REQUIRED_ITEM_FIELDS = ("dgi_item_type", "dgi_tax_group")
REQUIRED_CUSTOMER_FIELDS = ("dgi_client_type",)


def validate_dgi_fields(doc, method=None):
    """Block submission unless every DGI-required field is populated.

    We deliberately run this on `validate` (not `before_submit`) so users
    get the warning while editing, not when they're trying to submit.
    """
    settings = frappe.get_single("DGI Settings")
    if not settings.environment:
        return  # DGI not configured yet — don't block ERPNext at all.

    missing: list[str] = []
    for f in REQUIRED_INVOICE_FIELDS:
        if not doc.get(f):
            missing.append(_("Sales Invoice: {0}").format(f))

    for row in doc.items or []:
        for f in REQUIRED_ITEM_FIELDS:
            if not row.get(f):
                missing.append(_("Item row {0}: {1}").format(row.idx, f))

    customer = frappe.get_doc("Customer", doc.customer)
    for f in REQUIRED_CUSTOMER_FIELDS:
        if not customer.get(f):
            missing.append(_("Customer {0}: {1}").format(customer.name, f))

    pos_code = mapping.resolve_pos_for_invoice(doc)
    if not pos_code:
        missing.append(_("No DGI eMCF POS resolvable for this invoice."))

    if missing:
        frappe.throw(
            _("DGI compliance: please fill the following before submitting:")
            + "<ul><li>" + "</li><li>".join(missing) + "</li></ul>"
        )


def on_submit_certify(doc, method=None):
    """If auto-certify is enabled, fire-and-forget the certification call.

    We enqueue rather than blocking the user — Frappe Cloud may kill long
    foreground jobs, and we don't want a slow DGI response to roll back a
    valid Sales Invoice.
    """
    settings = frappe.get_single("DGI Settings")
    if not settings.environment:
        return
    if not int(settings.auto_normalize_on_submit or 0):
        return

    frappe.enqueue(
        "dgi_compliance.api.invoice.certify_sales_invoice",
        queue="long",
        timeout=180,
        sales_invoice=doc.name,
    )


def on_cancel_dgi(doc, method=None):
    """Mirror an ERPNext cancellation to DGI if the invoice had been certified."""
    if not doc.get("dgi_code_def"):
        return
    settings = frappe.get_single("DGI Settings")
    if not int(settings.auto_cancel_on_cancel or 0):
        return
    frappe.enqueue(
        "dgi_compliance.api.invoice.cancel_certification",
        queue="long",
        timeout=120,
        sales_invoice=doc.name,
        reason=_("ERPNext invoice cancelled"),
    )


def validate_customer_dgi_fields(doc, method=None):
    """Hard requirement: every Customer must have a DGI client type."""
    settings = frappe.get_single("DGI Settings")
    if not settings.environment:
        return
    if not doc.get("dgi_client_type"):
        frappe.msgprint(
            _("Set DGI Client Type on customer {0} before issuing DGI-certified invoices.")
            .format(doc.name),
            indicator="orange",
            alert=True,
        )


# ---------------------------------------------------------------------------
# Whitelisted shortcuts for the form button
# ---------------------------------------------------------------------------

@frappe.whitelist()
def manual_certify(sales_invoice: str):
    return invoice_api.certify_sales_invoice(sales_invoice, force=True)


@frappe.whitelist()
def refresh_reference_data():
    from dgi_compliance.api.info import refresh_reference_data as _r
    return _r()
