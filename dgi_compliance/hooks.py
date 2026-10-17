app_name = "dgi_compliance"
app_title = "DGI Compliance"
app_publisher = "Helosystemes"
app_description = "DGI RDC e-MCF / facture normalisee pour ERPNext v16 (application independante, upgrade-safe)"
app_version = "5.0.0"
app_license = "MIT"
required_apps = ["erpnext"]

# --- Form scripts (normalize button, defaults) ---
doctype_js = {
    "Sales Invoice": "public/js/sales_invoice.js",
    "Sales Order": "public/js/sales_order.js",
}

# --- Document events on Sales Invoice (no core change) ---
# validate: credit-note type first, VAT ceiling (optional), matrices + legal controls, currency,
#           then the draft DGI-status manager (normalized drafts are frozen).
# before_submit: posting gate (normalization through the e-MCF, no exception).
# before_cancel / on_trash / before_update_after_submit: protection of normalized invoices.
doc_events = {
    "Sales Invoice": {
        "validate": [
            "dgi_compliance.edef.tasks.enforce_return_invoice_type",
            "dgi_compliance.edef.rounding.apply_vat_ceiling",
            "dgi_compliance.edef.matrix.validate_sales_invoice",
            "dgi_compliance.edef.matrix.validate_currency",
            "dgi_compliance.edef.tasks.manage_draft_normalization_status",
        ],
        "before_print": "dgi_compliance.edef.tasks.register_print",
        "before_submit": "dgi_compliance.edef.tasks.before_sales_invoice_submit",
        "on_submit": "dgi_compliance.edef.tasks.on_sales_invoice_submit",
        "before_cancel": "dgi_compliance.edef.tasks.before_sales_invoice_cancel",
        "on_cancel": "dgi_compliance.edef.tasks.on_sales_invoice_cancel",
        "on_trash": "dgi_compliance.edef.tasks.on_sales_invoice_trash",
        "before_update_after_submit": "dgi_compliance.edef.tasks.protect_fiscal_fields",
    }
}

# --- Jinja helpers for the print formats ---
jinja = {
    "methods": [
        # v5.0.0 normalized invoice + fiscal reports
        "dgi_compliance.edef.invoice_print.dgi_invoice_context",
        "dgi_compliance.dgi_compliance.doctype.dgi_rapport_fiscal.dgi_rapport_fiscal.dgi_report_context",
        # v3/v4 print format "DGI Sales Invoice" (kept for rollback)
        "dgi_compliance.edef.printutils.dgi_tax_summary",
        "dgi_compliance.edef.printutils.dgi_isf",
        "dgi_compliance.edef.printutils.dgi_pos_nid",
        "dgi_compliance.edef.printutils.dgi_amount_in_words",
        "dgi_compliance.edef.printutils.dgi_item_type",
        "dgi_compliance.edef.printutils.dgi_item_tax_group",
        "dgi_compliance.edef.printutils.dgi_invoice_lines",
        "dgi_compliance.edef.printutils.dgi_totals",
        "dgi_compliance.edef.printutils.dgi_type_label",
        "dgi_compliance.edef.printutils.dgi_client_registration",
        "dgi_compliance.edef.printutils.dgi_print_label",
        "dgi_compliance.edef.printutils.dgi_is_foreign",
        "dgi_compliance.edef.printutils.dgi_cur_rate",
    ],
}

after_install = "dgi_compliance.edef.seed.after_install"

scheduler_events = {
    "daily": [
        "dgi_compliance.edef.tasks.check_token_expiry",
        "dgi_compliance.edef.sync.scheduled_sync",
        "dgi_compliance.edef.audit.scheduled_purge",
    ],
}

# Technical audit entries only: the fiscal exchanges are never purged (see edef/audit.py).
default_log_clearing_doctypes = {
    "DGI Exchange Log": 180,
}

# --- Custom Fields shipped as fixtures (upgrade-safe). Re-export with:
#     bench --site <site> export-fixtures --app dgi_compliance
fixtures = [
    {"dt": "Role", "filters": [["name", "in", ["DGI MCF Agent"]]]},
    {"dt": "Custom Field", "filters": [["name", "in", [
        "Sales Invoice-custom_dgi_input_section",
        "Sales Invoice-dgi_invoice_type",
        "Sales Order-dgi_invoice_type",
        "Sales Invoice-custom_dgi_export",
        "Sales Invoice-custom_dgi_input_cb",
        "Sales Invoice-custom_dgi_reference",
        "Sales Invoice-custom_dgi_reference_type",
        "Sales Invoice-custom_dgi_reference_desc",
        "Sales Invoice-dgi_validation_override",
        "Sales Invoice-custom_dgi_comments_section",
        "Sales Invoice-dgi_comment_a",
        "Sales Invoice-dgi_comment_b",
        "Sales Invoice-dgi_comment_c",
        "Sales Invoice-dgi_comment_d",
        "Sales Invoice-custom_dgi_comments_cb",
        "Sales Invoice-dgi_comment_e",
        "Sales Invoice-dgi_comment_f",
        "Sales Invoice-dgi_comment_g",
        "Sales Invoice-dgi_comment_h",
        "Sales Invoice-dgi_section",
        "Sales Invoice-custom_dgi_status",
        "Sales Invoice-custom_dgi_uid",
        "Sales Invoice-custom_dgi_code_def",
        "Sales Invoice-custom_dgi_counters",
        "Sales Invoice-custom_dgi_nim",
        "Sales Invoice-custom_dgi_datetime",
        "Sales Invoice-custom_dgi_qr_code",
        "Sales Invoice-custom_dgi_qr_image",
        "Sales Invoice-custom_dgi_exchange_je",
        "Sales Invoice-custom_dgi_error",
        "Sales Invoice-custom_dgi_print_count",
        "Sales Invoice-custom_dgi_first_printed_on",
        "Sales Invoice-custom_dgi_last_printed_on",
        "Customer-dgi_customer_type",
        "Customer-dgi_registration_no",
        "Item-dgi_item_type",
        "POS Profile-dgi_point_of_sale",
        "Sales Invoice-dgi_mode_of_payment",
        "Sales Invoice-custom_dgi_normalized_on",
        "Sales Invoice-custom_dgi_operator",
        "Sales Invoice-custom_dgi_operator_name",
        "Sales Invoice-custom_dgi_payload_hash",
        "Sales Invoice-custom_dgi_response",
        "Sales Invoice Item-dgi_specific_tax",
        "Item-dgi_specific_tax",
        "Item-dgi_hors_champ",
        "Company-dgi_rccm",
        "Company-dgi_id_nat"
    ]]]},
]
