"""Correspondances ERPNext -> point de vente fiscal (v5.3.0, livrable B sections 3 and 8).

POS Profile.dgi_point_of_sale and Warehouse.dgi_point_of_sale may be changed only by a DGI Fiscal Manager
or a System Manager, and only towards a point de vente of the same company."""
import frappe
from frappe import _

from dgi_compliance.fiscal.router import is_fiscal_manager


def validate_mapping(doc, method=None):
    if frappe.flags.in_patch or frappe.flags.in_install or frappe.flags.in_migrate:
        return
    if not doc.meta.has_field("dgi_point_of_sale"):
        return
    changed = (doc.is_new() and doc.get("dgi_point_of_sale")) or (not doc.is_new() and doc.has_value_changed("dgi_point_of_sale"))
    if not changed:
        return
    if not is_fiscal_manager():
        frappe.throw(_("Seul un DGI Fiscal Manager ou un System Manager peut rattacher {0} a un point de vente fiscal.")
                     .format(_(doc.doctype)), frappe.PermissionError)
    pvf = doc.get("dgi_point_of_sale")
    if not pvf:
        return
    pvf_company = frappe.db.get_value("DGI Point of Sale", pvf, "company")
    if pvf_company and doc.get("company") and pvf_company != doc.company:
        frappe.throw(_("Le point de vente fiscal {0} appartient a la societe {1}, {2} a la societe {3}.")
                     .format(pvf, pvf_company, doc.name, doc.company))
