import frappe
from frappe.model.document import Document


class DGITaxGroup(Document):
    """e-DEF tax group catalog (A..P). The rate is refreshed from /api/info/taxGroups;
    descriptions/flags are user-editable. Used by Matrix D (Item Type vs Tax Group)."""
    pass
