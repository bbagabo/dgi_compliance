import frappe
from frappe.model.document import Document


class DGISettings(Document):
    def validate(self):
        if self.environment == "Production" and not (self.production_url or "").startswith("https://"):
            frappe.throw("Production URL must use HTTPS.")
        if self.environment == "Test" and not (self.test_url or "").startswith("https://"):
            frappe.throw("Test URL must use HTTPS.")
        if (self.max_retry_count or 0) < 0:
            frappe.throw("Max Retry Count cannot be negative.")
        if (self.connection_timeout or 0) <= 0:
            self.connection_timeout = 30
