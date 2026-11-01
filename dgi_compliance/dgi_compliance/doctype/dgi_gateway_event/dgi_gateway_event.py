import frappe
from frappe import _
from frappe.model.document import Document


class DGIGatewayEvent(Document):
    def on_trash(self):
        frappe.throw(_("Le journal des passerelles est en ajout seul."))
