import frappe
from frappe.model.document import Document


class DGIeMCFPOS(Document):
    def validate(self):
        # NIM uniqueness (mirrors the BC pattern from `Point Of Sales.CheckNIMUniqness`)
        if self.nim:
            other = frappe.db.exists("DGI eMCF POS",
                                     {"nim": self.nim, "name": ["!=", self.name]})
            if other:
                frappe.throw(f"NIM {self.nim} is already used by POS {other}.")
