"""v4.0.0: create the 'DGI MCF Agent' role BEFORE the DGI MCF Job doctype syncs
(its permissions reference the role). Idempotent."""
import frappe


def execute():
    if not frappe.db.exists("Role", "DGI MCF Agent"):
        frappe.get_doc({
            "doctype": "Role",
            "role_name": "DGI MCF Agent",
            "desk_access": 0,
        }).insert(ignore_permissions=True)
        frappe.db.commit()
