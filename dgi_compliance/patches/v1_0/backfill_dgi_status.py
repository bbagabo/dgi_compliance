"""
For sites that already had historical Sales Invoices before installing the
app: mark submitted invoices without a DGI UID as 'Normalized' so they are
visually distinct from new invoices going through the certification flow.
"""

import frappe


def execute():
    if not frappe.db.has_column("Sales Invoice", "dgi_status"):
        return
    frappe.db.sql(
        """
        UPDATE `tabSales Invoice`
        SET    dgi_status = 'Normalized'
        WHERE  docstatus = 1
          AND  IFNULL(dgi_uid, '') = ''
          AND  IFNULL(dgi_status, '') = ''
        """
    )
    frappe.db.commit()
