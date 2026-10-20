"""v5.0.3 - customer DGI type and Matrix C.

1. Remove the legacy custom field ``dgi_client_type`` (created on Customer by v1.0, Select
   PP/PM/ETR). The v3.0 clean-up only removed it from Sales Invoice. The only customer DGI type
   is now ``dgi_customer_type`` (Link to DGI Customer Type, list synchronised from the e-MCF).
   Before removal, a valid legacy value is copied into ``dgi_customer_type`` when the latter is
   empty. The database column is kept (no data is deleted); only the field disappears.
2. Matrix C is renamed "C - Invoice Type / Pays (adresse client)": the country now always comes
   from the customer's address (see edef.matrix.resolve_customer_country).

Idempotent.
"""
import frappe

from dgi_compliance.dgi_compliance.doctype.dgi_validation_matrix.dgi_validation_matrix import (
    MATRIX_C, MATRIX_C_OLD,
)

LEGACY = "dgi_client_type"


def execute():
    _copy_legacy_customer_type()
    _remove_legacy_field()
    _rename_matrix_c()
    frappe.clear_cache()


def _copy_legacy_customer_type():
    if not (frappe.db.has_column("Customer", LEGACY) and frappe.db.has_column("Customer", "dgi_customer_type")):
        return
    valid = set(frappe.get_all("DGI Customer Type", pluck="name"))
    rows = frappe.db.sql(
        f"""select name, `{LEGACY}` as legacy from `tabCustomer`
            where ifnull(`{LEGACY}`, '') != '' and ifnull(dgi_customer_type, '') = ''""", as_dict=True)
    for r in rows:
        code = (r.legacy or "").strip().upper()
        if code in valid:
            frappe.db.set_value("Customer", r.name, "dgi_customer_type", code, update_modified=False)


def _remove_legacy_field():
    for cf in frappe.get_all("Custom Field", filters={"fieldname": LEGACY}, pluck="name"):
        frappe.delete_doc("Custom Field", cf, ignore_permissions=True, force=True)
    for ps in frappe.get_all("Property Setter", filters={"field_name": LEGACY}, pluck="name"):
        frappe.delete_doc("Property Setter", ps, ignore_permissions=True, force=True)


def _rename_matrix_c():
    if frappe.db.exists("DGI Validation Matrix", {"matrix_type": MATRIX_C_OLD}):
        frappe.db.sql("update `tabDGI Validation Matrix` set matrix_type = %s where matrix_type = %s",
                      (MATRIX_C, MATRIX_C_OLD))
