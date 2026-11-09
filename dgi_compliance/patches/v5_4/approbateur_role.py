"""v5.4.0 (pre model sync) - role DGI Approbateur, referenced by the permissions of the DGI doctypes.

Approves or refuses what another person initiated (MCF proposed, Poste MCF enrolled, channel switch
requested); no configuration right. Created before the doctypes are synced (the fixtures come later)."""
import frappe

ROLE = "DGI Approbateur"


def execute():
    if not frappe.db.exists("Role", ROLE):
        frappe.get_doc({"doctype": "Role", "role_name": ROLE, "desk_access": 1}).insert(ignore_permissions=True)
