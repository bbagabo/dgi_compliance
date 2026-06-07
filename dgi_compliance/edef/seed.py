"""Seed the STATIC e-DEF catalogs (protocol constants, identical for every taxpayer) so the
reference data is present right after install/update without needing the network or a token.

Account-specific / evolving data (tax group VALUES, currency rates, points de vente) is NOT
seeded here on purpose: it is pulled live by dgi_compliance.edef.sync. Seeding is insert-only:
it never overwrites a value already refreshed from the DGI server.
"""
import frappe

# Source: GET /api/info/{invoiceTypes,paymentTypes,clientTypes,referenceTypes,itemTypes}
STATIC_CATALOGS = {
    "Invoice Type": [
        ("FV", "Facture de vente"),
        ("FT", "Facture d'acompte ou d'avance"),
        ("FA", "Facture d'avoir"),
        ("EV", "Facture de vente a l'exportation"),
        ("ET", "Facture d'acompte ou d'avance a l'exportation"),
        ("EA", "Facture d'avoir a l'exportation"),
    ],
    "Payment Type": [
        ("ESPECES", "ESPECES"),
        ("MOBILEMONEY", "MOBILE MONEY"),
        ("VIREMENT", "VIREMENT"),
        ("CARTEBANCAIRE", "CARTE BANCAIRE"),
        ("CHEQUES", "CHEQUES"),
        ("CREDIT", "CREDIT"),
        ("AUTRE", "AUTRE"),
    ],
    "Client Type": [
        ("PP", "Personne physique"),
        ("PM", "Personne morale"),
        ("PC", "Personne physique commercante"),
        ("PL", "Profession liberale"),
        ("AO", "Ambassades et organisations internationales"),
    ],
    "Reference Type": [
        ("COR", "Correction"),
        ("RAN", "Annulation"),
        ("RAM", "Avoir suite reprise"),
        ("RRR", "Remise, ristourne, rabais"),
    ],
    "Item Type": [
        ("BIE", "Biens"),
        ("SER", "Services"),
        ("TAX", "Taxes et redevances"),
    ],
}


def seed_static_catalogs(force: bool = False) -> dict:
    """Insert the static catalogs if missing. With force=True, also refresh their descriptions.
    Returns counts per category."""
    counts = {}
    for category, rows in STATIC_CATALOGS.items():
        n = 0
        for code, desc in rows:
            name = f"{category}::{code}"
            if frappe.db.exists("DGI Reference Value", name):
                if force:
                    frappe.db.set_value("DGI Reference Value", name, "description", desc)
                continue
            frappe.get_doc({
                "doctype": "DGI Reference Value",
                "category": category, "code": code, "description": desc,
            }).insert(ignore_permissions=True)
            n += 1
        counts[category] = n
    frappe.db.commit()
    return counts


# --------------------------------------------------------------------------- #
# v2 - dedicated mapping DocTypes (insert-only; never clobbers user edits)
# --------------------------------------------------------------------------- #

ITEM_TYPES = [
    ("BIE", "Biens"),
    ("SER", "Services"),
    ("TAX", "Taxes et redevances"),
]

# code, description, is_export, is_prepayment, is_credit_note, requires_reference
INVOICE_TYPES = [
    ("FV", "Facture de vente", 0, 0, 0, 0),
    ("FT", "Facture d'acompte ou d'avance", 0, 1, 0, 0),
    ("FA", "Facture d'avoir", 0, 0, 1, 1),
    ("EV", "Facture de vente a l'exportation", 1, 0, 0, 0),
    ("ET", "Facture d'acompte ou d'avance a l'exportation", 1, 1, 0, 0),
    ("EA", "Facture d'avoir a l'exportation", 1, 0, 1, 1),
]

# Matrix B defaults: code, desc, [bill, vat, contact, address, phone, email, registration]
_M = "Mandatory"
_O = "Optional"
CUSTOMER_TYPES = [
    ("PP", "Personne physique",                          [_O, _O, _O, _O, _O, _O, _O]),
    ("PM", "Personne morale",                            [_M, _M, _M, _M, _M, _M, _M]),
    ("PC", "Personne physique commercante",              [_M, _M, _M, _M, _M, _M, _O]),
    ("PL", "Profession liberale",                        [_M, _M, _M, _M, _M, _M, _M]),
    ("AO", "Ambassades et organisations internationales", [_M, _O, _M, _O, _O, _O, _M]),
]
_REQ_ORDER = ["req_bill_to_name", "req_vat_reg_no", "req_contact", "req_address",
              "req_phone", "req_email", "req_registration_no"]

TAX_GROUPS = list("ABCDEFGHIJKLMNOP")


def _insert_if_missing(doctype, name, values):
    if frappe.db.exists(doctype, name):
        return 0
    frappe.get_doc({"doctype": doctype, **values}).insert(ignore_permissions=True)
    return 1


def seed_mapping_doctypes(force: bool = False) -> dict:
    counts = {"item_types": 0, "invoice_types": 0, "customer_types": 0, "tax_groups": 0}
    for code, desc in ITEM_TYPES:
        counts["item_types"] += _insert_if_missing(
            "DGI Item Type", code, {"code": code, "description": desc, "source": "Static"})
    for code, desc, exp, pre, cre, ref in INVOICE_TYPES:
        counts["invoice_types"] += _insert_if_missing(
            "DGI Invoice Type", code,
            {"code": code, "description": desc, "source": "Static",
             "is_export": exp, "is_prepayment": pre, "is_credit_note": cre, "requires_reference": ref})
    for code, desc, reqs in CUSTOMER_TYPES:
        values = {"code": code, "description": desc, "source": "Static"}
        values.update(dict(zip(_REQ_ORDER, reqs)))
        counts["customer_types"] += _insert_if_missing("DGI Customer Type", code, values)
    for code in TAX_GROUPS:
        counts["tax_groups"] += _insert_if_missing(
            "DGI Tax Group", code, {"code": code, "description": f"Groupe de taxe {code}", "source": "Static"})
    frappe.db.commit()
    return counts


# --------------------------------------------------------------------------- #
# v2 - default validation matrices (A-F). Seeded only when the table is empty,
# so user customisations are never overwritten on migrate.
# --------------------------------------------------------------------------- #

# Matrix C: (invoice_type, vat_group, country_scope, status)
MATRIX_C_ROWS = [
    ("FV", "LOC", "CD", "Allow"), ("FT", "LOC", "CD", "Allow"), ("FA", "LOC", "CD", "Allow"),
    ("EV", "LOC", "CD", "Blocked"), ("ET", "LOC", "CD", "Blocked"), ("EA", "LOC", "CD", "Blocked"),
    ("FV", "FOR", "CD", "Blocked"), ("FT", "FOR", "CD", "Blocked"), ("FA", "FOR", "CD", "Blocked"),
    ("EV", "FOR", "CD", "Allow"), ("ET", "FOR", "CD", "Allow"), ("EA", "FOR", "CD", "Allow"),
    ("FT", "LOC", "Non-CD", "Blocked"), ("FA", "LOC", "Non-CD", "Blocked"),
    ("EV", "LOC", "Non-CD", "Allow"), ("ET", "LOC", "Non-CD", "Allow"), ("EA", "LOC", "Non-CD", "Allow"),
    ("FV", "FOR", "Non-CD", "Blocked"), ("FT", "FOR", "Non-CD", "Blocked"), ("FA", "FOR", "Non-CD", "Blocked"),
    ("EV", "FOR", "Non-CD", "Allow"), ("ET", "FOR", "Non-CD", "Allow"), ("EA", "FOR", "Non-CD", "Allow"),
]

# Matrix D baseline: permissive (All/Any -> Allow). Replace with the official DGI grid as needed.
MATRIX_D_ROWS = [("All", "Any", "Allow")]

# Matrix E: (invoice_type, item_type, status)
MATRIX_E_ROWS = [
    ("FA", "BIE", "Blocked"), ("FA", "SER", "Allow"), ("FA", "TAX", "Blocked"),
    ("EA", "BIE", "Blocked"), ("EA", "SER", "Allow"), ("EA", "TAX", "Blocked"),
]

# Matrix F: (credit_nature, item_type, status)
MATRIX_F_ROWS = [
    ("COR", "All", "Allow"), ("RAN", "All", "Allow"), ("RAM", "All", "Allow"),
    ("RRR", "BIE", "Blocked"), ("RRR", "SER", "Allow"), ("RRR", "TAX", "Allow"),
]

MATRIX_C = "C - Invoice Type / VAT Group / Country"
MATRIX_D = "D - Item Type / Tax Group"
MATRIX_E = "E - Prepayment / Item Type"
MATRIX_F = "F - Credit Nature / Item Type"


def seed_validation_matrix(force: bool = False) -> dict:
    if not force and frappe.db.count("DGI Validation Matrix") > 0:
        return {"skipped": True}
    n = 0
    for it, vg, cs, status in MATRIX_C_ROWS:
        frappe.get_doc({"doctype": "DGI Validation Matrix", "matrix_type": MATRIX_C,
                        "invoice_type": it, "vat_group": vg, "country_scope": cs,
                        "status": status, "enforcement": "Block", "is_active": 1}).insert(ignore_permissions=True)
        n += 1
    for itm, tg, status in MATRIX_D_ROWS:
        frappe.get_doc({"doctype": "DGI Validation Matrix", "matrix_type": MATRIX_D,
                        "item_type": itm, "tax_group": tg,
                        "status": status, "enforcement": "Block", "is_active": 1}).insert(ignore_permissions=True)
        n += 1
    for it, itm, status in MATRIX_E_ROWS:
        frappe.get_doc({"doctype": "DGI Validation Matrix", "matrix_type": MATRIX_E,
                        "invoice_type": it, "item_type": itm,
                        "status": status, "enforcement": "Block", "is_active": 1}).insert(ignore_permissions=True)
        n += 1
    for nat, itm, status in MATRIX_F_ROWS:
        frappe.get_doc({"doctype": "DGI Validation Matrix", "matrix_type": MATRIX_F,
                        "credit_nature": nat, "item_type": itm,
                        "status": status, "enforcement": "Block", "is_active": 1}).insert(ignore_permissions=True)
        n += 1
    frappe.db.commit()
    return {"seeded": n}


def seed_all(force: bool = False) -> dict:
    return {
        "reference_values": seed_static_catalogs(force=force),
        "mapping": seed_mapping_doctypes(force=force),
        "matrix": seed_validation_matrix(force=force),
    }


def after_install():
    seed_static_catalogs()
    seed_mapping_doctypes()
    seed_validation_matrix()
