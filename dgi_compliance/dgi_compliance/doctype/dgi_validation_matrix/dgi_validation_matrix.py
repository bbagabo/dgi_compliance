import frappe
from frappe import _
from frappe.model.document import Document

# Canonical matrix type labels (kept in sync with the Select options in the .json).
# Matrix C (Invoice Type x Country) was removed in v5.0.5 (patch v505 deletes its rows).
MATRIX_C_REMOVED = ("C - Invoice Type / Pays (adresse client)", "C - Invoice Type / Country",
                    "C - Invoice Type / VAT Group / Country")
MATRIX_D = "D - Item Type / Tax Group"
MATRIX_E = "E - Prepayment / Item Type"
MATRIX_F = "F - Credit Nature / Item Type"
MATRIX_TYPES = (MATRIX_D, MATRIX_E, MATRIX_F)


class DGIValidationMatrix(Document):
    """A single, user-editable combination rule. The matrix engine (edef.matrix) reads every
    active row and decides whether a Sales Invoice line/header combination is Allowed or Blocked.
    No combination is hard-coded: administrators add, edit, or deactivate rows freely."""

    def validate(self):
        # Normalise empty dimensions to their wildcard so lookups are predictable.
        if not self.invoice_type:
            self.invoice_type = "Any"
        if not self.item_type:
            self.item_type = "All"
        if not self.tax_group:
            self.tax_group = "Any"
        if not self.credit_nature:
            self.credit_nature = "Any"
        if self.matrix_type not in MATRIX_TYPES:
            frappe.throw(_("Type de matrice invalide: {0}").format(self.matrix_type))
