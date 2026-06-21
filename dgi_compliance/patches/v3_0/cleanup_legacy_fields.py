"""v3.0 upgrade patch.

1. ISF becomes a single source of truth in DGI Compliance Settings: migrate any Company-level
   ISF into Settings.isf (if empty) before dropping the Company field.
2. Remove the three legacy custom fields completely (client + server):
       Sales Invoice.dgi_client_type      (ancien "DGI Client Type")
       Sales Invoice.dgi_payment_type     (ancien "DGI Payment Type")
       Sales Invoice.custom_dgi_vat_group ("Groupe TVA (LOC/FOR)")
       Company.dgi_isf_number             (ISF desormais uniquement dans Settings)
3. Rebuild Matrix C without the LOC/FOR (VAT Group) dimension.
4. Ensure the default Customer Type mapping (native -> DGI) exists.

Idempotent: safe to run more than once.
"""
import frappe

LEGACY_FIELDS = [
    ("Sales Invoice", "dgi_client_type"),
    ("Sales Invoice", "dgi_payment_type"),
    ("Sales Invoice", "custom_dgi_vat_group"),
    ("Company", "dgi_isf_number"),
]

OLD_MATRIX_C = "C - Invoice Type / VAT Group / Country"


def execute():
    _migrate_company_isf_to_settings()
    _delete_legacy_fields()
    _rebuild_matrix_c()
    _seed_defaults()
    frappe.clear_cache()


def _migrate_company_isf_to_settings():
    if not frappe.db.has_column("Company", "dgi_isf_number"):
        return
    try:
        settings = frappe.get_single("DGI Compliance Settings")
    except Exception:
        return
    if (settings.get("isf") or "").strip():
        return
    rows = frappe.db.sql(
        "select dgi_isf_number from `tabCompany` where ifnull(dgi_isf_number,'') != '' limit 1")
    if rows and rows[0][0]:
        settings.isf = rows[0][0]
        settings.save(ignore_permissions=True)
        frappe.db.commit()


def _delete_legacy_fields():
    for dt, fn in LEGACY_FIELDS:
        for cf in frappe.get_all("Custom Field", filters={"dt": dt, "fieldname": fn}, pluck="name"):
            frappe.delete_doc("Custom Field", cf, ignore_permissions=True, force=True)
        for ps in frappe.get_all("Property Setter", filters={"doc_type": dt, "field_name": fn}, pluck="name"):
            frappe.delete_doc("Property Setter", ps, ignore_permissions=True, force=True)
        # Drop the orphaned column if it lingers.
        try:
            if frappe.db.has_column(dt, fn):
                frappe.db.sql_ddl(f"ALTER TABLE `tab{dt}` DROP COLUMN `{fn}`")
        except Exception:
            pass
    frappe.db.commit()


def _rebuild_matrix_c():
    # Drop the old VAT-group Matrix C rows...
    if frappe.db.exists("DGI Validation Matrix", {"matrix_type": OLD_MATRIX_C}):
        frappe.db.delete("DGI Validation Matrix", {"matrix_type": OLD_MATRIX_C})
        frappe.db.commit()
    # ...and reseed the new Invoice Type x Country grid if none exists yet.
    from dgi_compliance.edef.seed import MATRIX_C, MATRIX_C_ROWS
    if not frappe.db.exists("DGI Validation Matrix", {"matrix_type": MATRIX_C}):
        for it, cs, status in MATRIX_C_ROWS:
            frappe.get_doc({
                "doctype": "DGI Validation Matrix", "matrix_type": MATRIX_C,
                "invoice_type": it, "country_scope": cs,
                "status": status, "enforcement": "Block", "is_active": 1,
            }).insert(ignore_permissions=True)
        frappe.db.commit()


def _seed_defaults():
    from dgi_compliance.edef.seed import seed_mapping_doctypes, seed_customer_type_mapping
    seed_mapping_doctypes()
    seed_customer_type_mapping()
