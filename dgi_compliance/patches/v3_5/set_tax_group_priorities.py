"""v3.5.0: assign an explicit Priority to existing DGI Tax Group Map rows.

The selection order becomes data-driven (lower priority wins):
  10 = Item Tax Template (line-level)  -> checked first
  20 = Sales Taxes and Charges Template (invoice-level)
  90 = legacy rate-only rows (deprecated fallback)
Rows that already carry a non-zero priority are left untouched.
"""
import frappe


def execute():
    frappe.reload_doc("dgi_compliance", "doctype", "dgi_tax_group_map")
    rows = frappe.get_all(
        "DGI Tax Group Map",
        fields=["name", "priority", "item_tax_template", "sales_taxes_template"],
    )
    for r in rows:
        if int(r.get("priority") or 0) > 0:
            continue
        if r.get("item_tax_template"):
            prio = 10
        elif r.get("sales_taxes_template"):
            prio = 20
        else:
            prio = 90
        frappe.db.set_value("DGI Tax Group Map", r["name"], "priority", prio,
                            update_modified=False)
    frappe.db.commit()
