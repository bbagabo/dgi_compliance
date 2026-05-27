"""
Creates the DGI Settings single doctype with safe defaults.
Idempotent; safe to re-run on every Deploy.
"""

import frappe


DEFAULTS = {
    "environment": "Test",
    "production_url": "https://edef.dgirdc.cd",
    "test_url": "https://developper.dgirdc.cd/edef",
    "auto_normalize_on_submit": 1,
    "auto_cancel_on_cancel": 1,
    "enable_detailed_logging": 1,
    "log_retention_days": 90,
    "connection_timeout": 30,
    "max_retry_count": 5,
    "retry_interval_minutes": 60,
    "default_article_type": "VAR",
    "default_payment_type": "ESPECES",
    "default_client_type": "PP",
}


def execute():
    if not frappe.db.exists("DocType", "DGI Settings"):
        return  # ran before the doctype was synced
    settings = frappe.get_single("DGI Settings")
    dirty = False
    for k, v in DEFAULTS.items():
        if not settings.get(k):
            settings.set(k, v)
            dirty = True
    if dirty:
        settings.save(ignore_permissions=True)
        frappe.db.commit()
