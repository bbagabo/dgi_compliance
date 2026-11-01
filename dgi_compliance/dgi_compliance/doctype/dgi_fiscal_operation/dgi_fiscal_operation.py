import frappe
from frappe import _
from frappe.model.document import Document


class DGIFiscalOperation(Document):
    """Written only by the fiscal flow (dgi_compliance.fiscal.operations); read-only for users."""

    def on_trash(self):
        frappe.throw(_("Une operation fiscale ne se supprime pas (tracabilite)."))
