"""
Seeds a starter set of DGI Reference Data so a freshly-installed site is
immediately usable without internet access to DGI. The scheduled job
`refresh_reference_data` will overwrite these with the authoritative values
on its next run.
"""

import frappe


SEED = {
    "Invoice Type": [
        ("FV", "Facture de vente"),
        ("EV", "Facture de vente à l'exportation"),
        ("FT", "Facture d'acompte"),
        ("FA", "Facture d'avoir"),
        ("EA", "Facture d'avoir à l'exportation"),
        ("ET", "Facture d'acompte à l'exportation"),
    ],
    "Payment Type": [
        ("ESPECES",       "Espèces"),
        ("VIREMENT",      "Virement bancaire"),
        ("CARTEBANCAIRE", "Carte bancaire"),
        ("MOBILEMONEY",   "Mobile Money"),
        ("CHEQUE",        "Chèque"),
        ("CREDIT",        "Crédit"),
        ("AUTRE",         "Autre"),
    ],
    "Client Type": [
        ("PP",  "Personne physique"),
        ("PM",  "Personne morale"),
        ("ETR", "Étranger"),
    ],
    "Reference Type": [
        ("RAM", "Remboursement"),
        ("ANN", "Annulation"),
        ("AVO", "Avoir commercial"),
    ],
    "Item Type": [
        ("VAR", "Variable / standard"),
        ("FIX", "Prix fixe"),
        ("SRV", "Service"),
        ("EXO", "Exonéré"),
    ],
    "Tax Group": [
        ("A", "Standard 16%"),
        ("B", "Réduit"),
        ("C", "Zéro"),
        ("D", "Exonéré"),
    ],
}


def execute():
    if not frappe.db.exists("DocType", "DGI Reference Data"):
        return
    for category, rows in SEED.items():
        for code, desc in rows:
            if frappe.db.exists("DGI Reference Data",
                                {"category": category, "code": code}):
                continue
            frappe.get_doc({
                "doctype": "DGI Reference Data",
                "category": category,
                "code": code,
                "description": desc,
                "active": 1,
            }).insert(ignore_permissions=True)
    frappe.db.commit()
