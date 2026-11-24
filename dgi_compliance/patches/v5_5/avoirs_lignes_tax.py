"""v5.5.2 - a credit note (FA/EA) may carry TAX lines (groups L and N).

Until v5.5.1 the seeded Matrix E rows "FA x TAX" and "EA x TAX" were Blocked: an invoice with a prelevement sur
ventes (L) or a TVA specifique (N) could not be cancelled or credited, since the credit note repeats the lines
of the original invoice. Only the rows still in their seeded state (status Blocked, no custom message) are
switched to Allow: a rule written by an administrator is left as it is. Idempotent; the result is written to
the journal (DGI Gateway Event, MATRICE_E).
"""
import frappe


def execute():
    from dgi_compliance.dgi_compliance.doctype.dgi_validation_matrix.dgi_validation_matrix import MATRIX_E
    changed = []
    for row in frappe.get_all("DGI Validation Matrix",
                              filters={"matrix_type": MATRIX_E, "invoice_type": ["in", ["FA", "EA"]],
                                       "item_type": "TAX", "status": "Blocked"},
                              fields=["name", "invoice_type", "message"]):
        if (row.message or "").strip():
            continue
        frappe.db.set_value("DGI Validation Matrix", row.name, "status", "Allow", update_modified=False)
        changed.append(f"{row.invoice_type} x TAX")
    if not changed:
        return
    from dgi_compliance.gateway.events import log_event
    log_event(None, "MATRICE_E",
              "Mise a jour 5.5.2 : les avoirs peuvent reprendre les lignes TAX (groupes L et N) de la facture "
              "d'origine. Regles passees de Bloque a Autorise : " + ", ".join(changed) + ".",
              level="Info", details={"rules": changed})
