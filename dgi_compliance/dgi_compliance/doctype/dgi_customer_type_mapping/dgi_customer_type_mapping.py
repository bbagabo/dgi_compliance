import frappe
from frappe.model.document import Document


class DGICustomerTypeMapping(Document):
    """Child row mapping a native ERPNext Customer Type (Company/Individual/...) to an allowed
    DGI Customer Type code (PP/PM/PC/PL/AO). Read by edef.matrix (Matrix G) to validate invoices
    before normalization, and to auto-derive a missing DGI type from the native customer_type."""
    pass
