import frappe
from frappe.model.document import Document


class DGICurrencyRule(Document):
    """Child row of the DGI currency matrix (DGI Settings -> Currency Rules).

    Defines, per (currency, invoice type): whether the currency is allowed for billing, and which
    exchange rate to use when converting the invoice amounts to CDF for the e-DEF payload."""
    pass
