"""v5.4.2 - reports X / Z / A per point de vente fiscal.

The rows without any amount are hidden by default (setting report_hide_zero_rows): the new Check field has no
stored value on a site that is updated, it is switched on here. Reports already generated keep their data and
print as one block (they hold no detail per point de vente).
"""
import frappe


def execute():
    row = frappe.db.sql("select value from `tabSingles` where doctype=%s and field=%s",
                        ("DGI Compliance Settings", "report_hide_zero_rows"))
    if not row or row[0][0] in (None, ""):
        frappe.db.set_single_value("DGI Compliance Settings", "report_hide_zero_rows", 1, update_modified=False)
