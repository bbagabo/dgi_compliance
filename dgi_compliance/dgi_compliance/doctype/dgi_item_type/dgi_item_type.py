import frappe
from frappe.model.document import Document


class DGIItemType(Document):
    """e-DEF item type catalog (BIE / SER / TAX). Synced from /api/info/itemTypes,
    seeded statically, and used by the validation matrices. Never modifies ERPNext core."""
    pass
