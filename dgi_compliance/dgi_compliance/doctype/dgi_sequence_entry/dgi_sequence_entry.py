import frappe
from frappe import _
from frappe.model.document import Document


class DGISequenceEntry(Document):
    """Register of the sequential references (append only, fiscal.sequence)."""

    def validate(self):
        if not self.flags.get("dgi_sequence"):
            frappe.throw(_("Le registre des references est tenu par le SFE (ajout seul)."))

    def on_trash(self):
        frappe.throw(_("Le registre des references est en ajout seul."))


def on_doctype_update():
    # series control (fiscal.sequence.check_sequence) and the defensive max(number) read
    frappe.db.add_index("DGI Sequence Entry", ["sequence_code", "year", "number"])
