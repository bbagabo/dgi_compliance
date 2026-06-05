app_name = "dgi_compliance"
app_title = "DGI Compliance"
app_publisher = "DGI Compliance"
app_description = "DGI RDC e-MCF/e-DEF fiscal compliance for ERPNext v16 (upgrade-safe)"
app_version = "1.1.1"
app_license = "MIT"
required_apps = ["erpnext"]

# --- Document events: hook into Sales Invoice without touching core ---
doc_events = {
    "Sales Invoice": {
        "on_submit": "dgi_compliance.edef.tasks.on_sales_invoice_submit",
        "on_cancel": "dgi_compliance.edef.tasks.on_sales_invoice_cancel",
    }
}

# --- Scheduler: one daily entry; cadence (daily/weekly/monthly) is decided inside the job
#     by reading DGI Compliance Settings, so the user can change it without redeploying. ---
scheduler_events = {
    "daily": [
        "dgi_compliance.edef.tasks.check_token_expiry",
    ],
}

# --- Custom Fields shipped as fixtures (upgrade-safe). Exported with:
#     bench --site <site> export-fixtures --app dgi_compliance ---
fixtures = [
    {
        "dt": "Custom Field",
        "filters": [["name", "in", [
            "Sales Invoice-dgi_section",
            "Sales Invoice-custom_dgi_uid",
            "Sales Invoice-custom_dgi_qr_code",
            "Sales Invoice-custom_dgi_code_def",
            "Sales Invoice-custom_dgi_counters",
            "Sales Invoice-custom_dgi_nim",
            "Sales Invoice-custom_dgi_datetime",
            "Sales Invoice-custom_dgi_status",
            "Sales Invoice-custom_dgi_error",
        ]]],
    },
]
