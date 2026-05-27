"""
Adds every Custom Field the app needs on top of standard ERPNext doctypes.

Why a patch *and* a fixture?  On Frappe Cloud, `developer_mode = 0`, so
custom fields cannot be created from the live Desk. The fixture covers the
common case (`bench install-app`); the patch is the safety net for any
site where the fixture was skipped or for fields added in a later release.

Idempotent: `create_custom_fields(..., update=True)` updates the field if
it already exists rather than failing.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


CUSTOM_FIELDS = {
    "Sales Invoice": [
        {
            "fieldname": "dgi_section",
            "label": "DGI / e-DEF",
            "fieldtype": "Section Break",
            "insert_after": "more_info",
            "collapsible": 1,
        },
        {
            "fieldname": "dgi_emcf_pos",
            "label": "DGI eMCF POS",
            "fieldtype": "Link",
            "options": "DGI eMCF POS",
            "insert_after": "dgi_section",
        },
        {
            "fieldname": "dgi_invoice_type",
            "label": "DGI Invoice Type",
            "fieldtype": "Select",
            "options": "FV\nEV\nFT\nFA\nEA\nET",
            "default": "FV",
            "insert_after": "dgi_emcf_pos",
        },
        {
            "fieldname": "dgi_credit_note_type",
            "label": "DGI Credit Note Type",
            "fieldtype": "Select",
            "options": "\nFA\nEA",
            "depends_on": "eval:doc.is_return",
            "insert_after": "dgi_invoice_type",
        },
        {
            "fieldname": "dgi_payment_type",
            "label": "DGI Payment Type",
            "fieldtype": "Data",
            "insert_after": "dgi_credit_note_type",
        },
        {
            "fieldname": "dgi_uid",
            "label": "DGI UID",
            "fieldtype": "Data",
            "read_only": 1,
            "insert_after": "dgi_payment_type",
        },
        {
            "fieldname": "dgi_code_def",
            "label": "Code DEF/DGI",
            "fieldtype": "Data",
            "read_only": 1,
            "insert_after": "dgi_uid",
        },
        {
            "fieldname": "dgi_qr_code",
            "label": "QR Code content",
            "fieldtype": "Small Text",
            "read_only": 1,
            "insert_after": "dgi_code_def",
        },
        {
            "fieldname": "dgi_counters",
            "label": "DGI Counters",
            "fieldtype": "Data",
            "read_only": 1,
            "insert_after": "dgi_qr_code",
        },
        {
            "fieldname": "dgi_status",
            "label": "DGI Status",
            "fieldtype": "Select",
            "options": "\nPending\nNormalized\nCancelled\nFailed",
            "read_only": 1,
            "insert_after": "dgi_counters",
        },
        {
            "fieldname": "dgi_reference",
            "label": "DGI Reference (original code)",
            "fieldtype": "Data",
            "depends_on": "eval:doc.is_return",
            "insert_after": "dgi_status",
        },
        {
            "fieldname": "dgi_reference_type",
            "label": "DGI Reference Type",
            "fieldtype": "Data",
            "depends_on": "eval:doc.is_return",
            "insert_after": "dgi_reference",
        },
        {
            "fieldname": "dgi_reference_desc",
            "label": "DGI Reference Description",
            "fieldtype": "Data",
            "depends_on": "eval:doc.is_return",
            "insert_after": "dgi_reference_type",
        },
    ],
    "Sales Invoice Item": [
        {
            "fieldname": "dgi_item_type",
            "label": "DGI Item Type",
            "fieldtype": "Data",
            "default": "VAR",
            "insert_after": "item_name",
        },
        {
            "fieldname": "dgi_tax_group",
            "label": "DGI Tax Group",
            "fieldtype": "Select",
            "options": "A\nB\nC\nD\nE\nF\nG\nH\nI\nJ\nK\nL\nM\nN\nO\nP",
            "default": "A",
            "insert_after": "dgi_item_type",
        },
    ],
    "Customer": [
        {
            "fieldname": "dgi_client_type",
            "label": "DGI Client Type",
            "fieldtype": "Select",
            "options": "PP\nPM\nETR",
            "default": "PP",
            "insert_after": "tax_id",
        },
    ],
    "POS Profile": [
        {
            "fieldname": "dgi_emcf_pos",
            "label": "DGI eMCF POS",
            "fieldtype": "Link",
            "options": "DGI eMCF POS",
            "insert_after": "warehouse",
        },
    ],
    "Warehouse": [
        {
            "fieldname": "dgi_emcf_pos",
            "label": "DGI eMCF POS",
            "fieldtype": "Link",
            "options": "DGI eMCF POS",
            "insert_after": "warehouse_type",
        },
    ],
    "Mode of Payment": [
        {
            "fieldname": "dgi_payment_type",
            "label": "DGI Payment Type Code",
            "fieldtype": "Data",
            "insert_after": "type",
        },
    ],
}


def execute():
    # Bail early if the DGI doctypes haven't synced yet — patches run before
    # `before_install` in some Frappe versions.
    if not frappe.db.exists("DocType", "DGI eMCF POS"):
        return
    create_custom_fields(CUSTOM_FIELDS, update=True)
    frappe.db.commit()
