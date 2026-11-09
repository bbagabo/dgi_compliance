"""v5.4.0 - reference sequentielle par point de vente fiscal (fiscal.sequence) and separation of duties.

* settings: the reference is on, one series per point de vente fiscal, label "Réf. PDV", code SIEGE for
  invoices without point de vente; activation date = now. Invoices normalized BEFORE the update keep no
  reference (they were printed without one): nothing is numbered retroactively;
* every point de vente fiscal receives its sequence code (its 'Code magasin' when valid and free, else
  5 letters of its name + 2 digits), editable until the first reference is given;
* the two-person rules stay as configured (default on). Nothing else changes for the existing data.
"""
import frappe
from frappe.utils import now_datetime


def _single(field):
    row = frappe.db.sql("select value from `tabSingles` where doctype=%s and field=%s",
                        ("DGI Compliance Settings", field))
    return row[0][0] if row else None


def execute():
    from dgi_compliance.fiscal import sequence as S
    frappe.clear_cache(doctype="DGI Point of Sale")
    for pvf in frappe.get_all("DGI Point of Sale", fields=["name", "pos_name", "pos_code", "sequence_code"],
                              order_by="creation asc"):
        code = S.clean_code(pvf.sequence_code)
        if S.CODE_RE.match(code) and not S.code_in_use(code, exclude_pvf=pvf.name):
            if code != pvf.sequence_code:
                frappe.db.set_value("DGI Point of Sale", pvf.name, "sequence_code", code, update_modified=False)
            continue
        code = S.suggest_code(pvf.pos_name or pvf.name, pvf.pos_code, exclude_pvf=pvf.name)
        frappe.db.set_value("DGI Point of Sale", pvf.name, "sequence_code", code, update_modified=False)
    defaults = {"sequence_enabled": 1, "sequence_scope": S.SCOPE_PVF, "sequence_print_label": S.DEFAULT_LABEL}
    for field, value in defaults.items():
        if _single(field) in (None, ""):
            frappe.db.set_single_value("DGI Compliance Settings", field, value)
    code = S.clean_code(_single("sequence_default_code")) or S.DEFAULT_CODE
    if frappe.db.exists("DGI Point of Sale", {"sequence_code": code}):
        code = next(f"{S.DEFAULT_CODE}{i:02d}" for i in range(1, 100)
                    if not frappe.db.exists("DGI Point of Sale", {"sequence_code": f"{S.DEFAULT_CODE}{i:02d}"}))
    frappe.db.set_single_value("DGI Compliance Settings", "sequence_default_code", code)
    if not _single("sequence_start_on"):
        frappe.db.set_single_value("DGI Compliance Settings", "sequence_start_on", now_datetime())
    frappe.clear_document_cache("DGI Compliance Settings", "DGI Compliance Settings")
