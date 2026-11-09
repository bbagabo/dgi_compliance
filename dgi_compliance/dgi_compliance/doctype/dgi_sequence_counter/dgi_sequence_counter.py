import frappe
from frappe import _
from frappe.model.document import Document


class DGISequenceCounter(Document):
    """Counter of the sequential reference (Ref. PDV), one row per code and year (fiscal.sequence)."""

    def validate(self):
        if not self.flags.get("dgi_sequence"):
            frappe.throw(_("Le compteur de reference est tenu par le SFE : il ne se modifie pas a la main."))

    def on_trash(self):
        frappe.throw(_("Un compteur de reference ne se supprime pas."))
