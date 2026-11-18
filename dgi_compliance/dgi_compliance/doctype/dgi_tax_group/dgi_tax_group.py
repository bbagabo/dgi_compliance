from frappe.model.document import Document

from dgi_compliance.edef.calc import is_reserved_group, reserved_label


class DGITaxGroup(Document):
    """Tax group catalog (FACNO groupes de taxation v2.0: A-S applicable, T-Z reserved).
    The rate is refreshed from /api/info/taxGroups; descriptions/flags are user-editable.
    Used by Matrix D (Item Type vs Tax Group).

    v5.5.0: a reserved group (T-Z, "Reserve 1..7") is always flagged reserved and inactive, even
    when the e-MCF lists it in /api/info/taxGroups: no item may be recorded in it."""

    def validate(self):
        self.code = (self.code or "").strip().upper()
        reserved = is_reserved_group(self.code)
        self.is_reserved = 1 if reserved else 0
        if reserved:
            self.is_active = 0
            self.is_vat = 0
            if not self.description or self.description == self.code:
                self.description = reserved_label(self.code)
