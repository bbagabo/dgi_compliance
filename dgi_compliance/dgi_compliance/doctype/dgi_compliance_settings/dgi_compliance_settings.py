import frappe
from frappe.model.document import Document


class DGIComplianceSettings(Document):
    def base_url(self) -> str:
        if (self.environment or "Test") == "Production":
            return (self.base_url_production or "").rstrip("/")
        return (self.base_url_test or "").rstrip("/")

    def get_token(self) -> str:
        # Password fields are decrypted via get_password()
        return self.get_password("token", raise_exception=False) or ""

    def payment_type_for(self, mode_of_payment: str) -> str:
        for row in (self.payment_mode_map or []):
            if row.erpnext_mode_of_payment == mode_of_payment:
                return row.edef_payment_type
        return self.default_payment_type or "ESPECES"

    def tax_group_for(self, item_tax_template: str | None, rate: float | None) -> str:
        for row in (self.tax_group_map or []):
            if item_tax_template and row.item_tax_template == item_tax_template:
                return row.edef_tax_group
        if rate is not None:
            for row in (self.tax_group_map or []):
                if not row.item_tax_template and row.tax_rate is not None and abs(float(row.tax_rate) - float(rate)) < 1e-6:
                    return row.edef_tax_group
        return self.default_tax_group or "A"

    # ---- Native Customer Type <-> DGI Type mapping (Matrix G) ----

    def dgi_types_for_customer_type(self, erpnext_customer_type: str | None) -> list[str]:
        """Return the list of DGI Customer Type codes allowed for a native ERPNext customer_type."""
        if not erpnext_customer_type:
            return []
        ect = str(erpnext_customer_type).strip().lower()
        out = []
        for row in (self.customer_type_map or []):
            if str(row.erpnext_customer_type or "").strip().lower() == ect and row.dgi_customer_type:
                out.append(row.dgi_customer_type)
        return out

    def default_dgi_type_for_customer_type(self, erpnext_customer_type: str | None) -> str | None:
        """Return the DGI code flagged as default for a native customer_type (else the only/first one)."""
        if not erpnext_customer_type:
            return None
        ect = str(erpnext_customer_type).strip().lower()
        rows = [r for r in (self.customer_type_map or [])
                if str(r.erpnext_customer_type or "").strip().lower() == ect and r.dgi_customer_type]
        if not rows:
            return None
        for r in rows:
            if r.get("is_default"):
                return r.dgi_customer_type
        return rows[0].dgi_customer_type if len(rows) == 1 else None


def get_settings() -> "DGIComplianceSettings":
    return frappe.get_cached_doc("DGI Compliance Settings")
