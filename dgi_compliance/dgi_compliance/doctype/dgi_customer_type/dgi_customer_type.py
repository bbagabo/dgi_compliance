import frappe
from frappe.model.document import Document

# Map each "req_*" field on this DocType to the Sales-Invoice / Customer attribute that
# satisfies it. Used by edef.matrix.validate_customer_fields (Matrix B).
REQUIREMENT_FIELDS = {
    "req_bill_to_name": "Nom / Raison sociale",
    "req_vat_reg_no": "NIF / N TVA",
    "req_contact": "Contact",
    "req_address": "Adresse",
    "req_phone": "Telephone",
    "req_email": "E-mail",
    "req_registration_no": "N d'enregistrement",
}


class DGICustomerType(Document):
    """e-DEF client type catalog (PP / PM / PC / PL / AO) + per-type mandatory-field matrix
    (Matrix B). Controls which customer-card fields are required when invoicing this type."""

    def mandatory_fields(self):
        """Return the list of req_* fieldnames that are set to 'Mandatory'."""
        return [f for f in REQUIREMENT_FIELDS if (self.get(f) or "Optional") == "Mandatory"]
