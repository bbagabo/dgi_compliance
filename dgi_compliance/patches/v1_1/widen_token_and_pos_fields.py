"""
v1.1.0 migration.

1. Widen the DGI eMCF POS `token` column to varchar(500). The v1.0.0
   doctype shipped the Bearer Token as a Password field with the Frappe
   default length (140), which truncated long DGI JWTs. Setting length=500
   on the field handles new sites; this patch fixes columns already created
   at length 140.

2. The new read-only fields `setup_complete` and `last_setup_check` are
   created from the doctype JSON during model sync; nothing to do here for
   them beyond making sure the sync ran.

Idempotent and safe to re-run.
"""

import frappe


def execute():
    if not frappe.db.exists("DocType", "DGI eMCF POS"):
        return

    # Resync the doctype so the new length / fields land in the schema.
    frappe.reload_doc("dgi_compliance", "doctype", "dgi_emcf_pos")

    # Force the underlying column to varchar(500) in case the in-place
    # alter from the reload kept the old width.
    try:
        col = frappe.db.sql(
            """
            SELECT CHARACTER_MAXIMUM_LENGTH
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'tabDGI eMCF POS'
              AND COLUMN_NAME = 'token'
            """
        )
        current = col[0][0] if col else None
        if current is not None and int(current) < 500:
            frappe.db.sql_ddl(
                "ALTER TABLE `tabDGI eMCF POS` MODIFY `token` VARCHAR(500)"
            )
    except Exception:
        # Non-fatal: on some DB backends the field-length sync is enough.
        frappe.log_error(
            title="DGI v1.1.0 token column widen skipped",
            message=frappe.get_traceback(),
        )

    frappe.db.commit()
