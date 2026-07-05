import frappe
from frappe.model.document import Document


class DGIInvoiceType(Document):
    """e-DEF invoice type catalog (FV / FT / FA / EV / ET / EA). Drives invoice behavior
    and the validation matrices. The behavior flags (export/prepayment/credit) are
    user-editable so new DGI rules can be reflected without code changes."""
    pass
