"""
hooks.py — entry-points wired into Frappe.

Everything here is additive: we do NOT replace any core controller. The
ERPNext Sales Invoice and Customer DocTypes remain untouched on disk; we
only register doc_events callbacks, custom fields (via fixtures + patch),
client scripts and scheduled jobs.

This is the contract that lets an `app update` of ERPNext core flow through
without breaking us.
"""

from . import __version__ as app_version  # noqa: F401

app_name = "dgi_compliance"
app_title = "DGI Compliance"
app_publisher = "HeloSystems"
app_description = "DGI RDC e-DEF (Facture Normalisée) certification for ERPNext"
app_email = "contact@helosystems.com"
app_license = "MIT"

# -----------------------------------------------------------------------------
# Includes (Desk JS / CSS)
# -----------------------------------------------------------------------------
app_include_css = "/assets/dgi_compliance/css/dgi.css"
app_include_js = "/assets/dgi_compliance/js/sales_invoice_dgi.js"

# Per-DocType form scripts (loaded only when the form opens)
doctype_js = {
    "Sales Invoice": "public/js/sales_invoice_dgi.js",
}

# -----------------------------------------------------------------------------
# Document events — additive only, no overrides of core controllers.
# -----------------------------------------------------------------------------
doc_events = {
    "Sales Invoice": {
        "validate": "dgi_compliance.overrides.sales_invoice.validate_dgi_fields",
        "on_submit": "dgi_compliance.overrides.sales_invoice.on_submit_certify",
        "on_cancel": "dgi_compliance.overrides.sales_invoice.on_cancel_dgi",
    },
    "Customer": {
        "validate": "dgi_compliance.overrides.sales_invoice.validate_customer_dgi_fields",
    },
}

# -----------------------------------------------------------------------------
# Scheduled jobs — Frappe Cloud picks these up; do NOT add a system-level cron.
# -----------------------------------------------------------------------------
scheduler_events = {
    "hourly": [
        "dgi_compliance.tasks.retry_pending_invoices",
    ],
    "daily": [
        "dgi_compliance.tasks.refresh_reference_data",
        "dgi_compliance.tasks.check_pos_token_validity",
        "dgi_compliance.tasks.prune_old_logs",
    ],
    "cron": {
        # Every 15 minutes — light keep-alive ping of /status
        "*/15 * * * *": [
            "dgi_compliance.tasks.ping_api_status",
        ],
    },
}

# -----------------------------------------------------------------------------
# Fixtures — Custom Fields are *also* installed via patch (idempotent).
# Fixtures keep them in source control; the patch is the safety net when
# fixtures are skipped during a cloud deploy.
# -----------------------------------------------------------------------------
fixtures = [
    {
        "doctype": "Custom Field",
        "filters": [
            ["name", "like", "%-dgi_%"],
        ],
    },
]

# -----------------------------------------------------------------------------
# Whitelisted API methods exposed to the Desk (frappe.call)
# -----------------------------------------------------------------------------
override_whitelisted_methods = {}

# -----------------------------------------------------------------------------
# After-install hook — creates the default Single doctype values if missing.
# Safe to re-run.
# -----------------------------------------------------------------------------
after_install = "dgi_compliance.patches.v1_0.create_default_settings.execute"
