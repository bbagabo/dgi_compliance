app_name = "dgi_compliance"
app_title = "DGI Compliance"
app_publisher = "DGI Compliance"
app_description = "DGI RDC e-MCF/e-DEF fiscal compliance for ERPNext v16 (upgrade-safe)"
app_version = "2.0.1"
app_license = "MIT"
required_apps = ["erpnext"]

# --- Form scripts loaded per DocType (retry button on Sales Invoice) ---
doctype_js = {
    "Sales Invoice": "public/js/sales_invoice.js",
}

# --- Document events: hook into Sales Invoice without touching core ---
# validate runs the VAT ceiling FIRST (adjusts totals), then the matrix locking engine.
doc_events = {
    "Sales Invoice": {
        "validate": [
            "dgi_compliance.edef.rounding.apply_vat_ceiling",
            "dgi_compliance.edef.matrix.validate_sales_invoice",
        ],
        "on_submit": "dgi_compliance.edef.tasks.on_sales_invoice_submit",
        "on_cancel": "dgi_compliance.edef.tasks.on_sales_invoice_cancel",
    }
}

# --- Seed static reference catalogs right after install ---
after_install = "dgi_compliance.edef.seed.after_install"

# --- Scheduler: one daily entry per job; the cadence is decided inside each job from Settings. ---
scheduler_events = {
    "daily": [
        "dgi_compliance.edef.tasks.check_token_expiry",
        "dgi_compliance.edef.sync.scheduled_sync",
        "dgi_compliance.edef.audit.scheduled_purge",
    ],
}

# --- Register the audit log for Frappe's Log Settings auto-clearing (days). ---
default_log_clearing_doctypes = {
    "DGI Exchange Log": 180,
}

# --- Custom Fields shipped as fixtures (upgrade-safe). Re-export with:
#     bench --site <site> export-fixtures --app dgi_compliance ---
fixtures = [
    {
        "dt": "Custom Field",
        "filters": [["name", "in", [
            # Sales Invoice - input / classification
            "Sales Invoice-custom_dgi_input_section",
            "Sales Invoice-dgi_invoice_type",
            "Sales Invoice-custom_dgi_vat_group",
            "Sales Invoice-custom_dgi_export",
            "Sales Invoice-custom_dgi_input_cb",
            "Sales Invoice-custom_dgi_reference",
            "Sales Invoice-custom_dgi_reference_type",
            "Sales Invoice-custom_dgi_reference_desc",
            "Sales Invoice-dgi_validation_override",
            # Sales Invoice - normalization results
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
            # Other DocTypes (upgrade-safe links)
            "Customer-dgi_customer_type",
            "Item-dgi_item_type",
            "Company-dgi_isf_number",
            "POS Profile-dgi_point_of_sale",
        ]]],
    },
]
