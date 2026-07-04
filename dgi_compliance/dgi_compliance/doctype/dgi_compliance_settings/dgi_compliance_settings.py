import frappe
from frappe import _
from frappe.model.document import Document


class DGIComplianceSettings(Document):
    def validate(self):
        # Security: warn (do not block) when the active e-DEF endpoint is not HTTPS.
        url = self.base_url()
        if url and not url.lower().startswith("https://"):
            frappe.msgprint(_("Avertissement securite DGI: l'URL e-DEF active n'est pas en HTTPS "
                              "({0}). Le jeton transiterait en clair.").format(url),
                            indicator="orange", title=_("Securite"))

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
        """Legacy resolution (line template / rate only). Prefer tax_group_for_invoice()."""
        for row in (self.tax_group_map or []):
            if item_tax_template and row.item_tax_template == item_tax_template:
                return row.edef_tax_group
        if rate is not None:
            for row in (self.tax_group_map or []):
                if not row.item_tax_template and row.tax_rate is not None and abs(float(row.tax_rate) - float(rate)) < 1e-6:
                    return row.edef_tax_group
        return self.default_tax_group or "A"

    def tax_group_for_invoice(self, doc, item=None, strict: bool = False) -> str | None:
        """v3.4 resolution of the e-DEF Tax Group (A-P) for an invoice line.

        Order (an existing mapping ALWAYS wins over the default group):
          1. Item Tax Template of the LINE      -> mapped e-DEF Tax Group;
          2. Sales Taxes and Charges Template
             of the INVOICE (taxes_and_charges) -> mapped e-DEF Tax Group;
          3. Default Tax Group ONLY when NO tax is specified on the invoice
             (no line template, no invoice template, no tax rows).

        strict=True: when a tax IS specified but has no mapping, return None so the
        caller can raise an explicit error instead of silently sending the default.
        """
        def get(d, k):
            if d is None:
                return None
            return d.get(k) if isinstance(d, dict) else (d.get(k) if hasattr(d, "get") else getattr(d, k, None))

        itt = get(item, "item_tax_template")
        stt = get(doc, "taxes_and_charges")

        # 1) line-level mapping (Item Tax Template) has priority
        if itt:
            for row in (self.tax_group_map or []):
                if row.item_tax_template and row.item_tax_template == itt:
                    return row.edef_tax_group

        # 2) invoice-level mapping (Sales Taxes and Charges Template)
        if stt:
            for row in (self.tax_group_map or []):
                tpl = row.get("sales_taxes_template") if hasattr(row, "get") else getattr(row, "sales_taxes_template", None)
                if tpl and tpl == stt:
                    return row.edef_tax_group

        # 3) default group only when no tax is specified at all
        has_tax_rows = bool(get(doc, "taxes") or [])
        if not itt and not stt and not has_tax_rows:
            return self.default_tax_group or "A"

        # A tax exists on the invoice but no mapping matches.
        if strict:
            return None
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


    # ---- Currency matrix (authorized currencies + conversion rule) ----

    def currency_rule_for(self, currency, invoice_type=None):
        """Most specific active rule for (currency, invoice_type): an exact invoice_type wins over
        an 'Any' rule. Returns None when the currency is not listed at all."""
        best, best_spec = None, -1
        for r in (self.currency_rules or []):
            if (r.currency or "") != (currency or ""):
                continue
            it = (r.invoice_type or "Any")
            if it not in ("Any", "", None) and invoice_type and it != invoice_type:
                continue
            spec = 1 if it not in ("Any", "", None) else 0
            if spec > best_spec:
                best, best_spec = r, spec
        return best

    def currency_allowed(self, currency, invoice_type=None) -> bool:
        """True when the currency may be used. No rules configured => allow everything (compat)."""
        if not (self.currency_rules or []):
            return True
        rule = self.currency_rule_for(currency, invoice_type)
        if rule is None:
            return False  # currency absent from the matrix => not allowed
        return bool(rule.is_allowed)


def get_settings() -> "DGIComplianceSettings":
    return frappe.get_cached_doc("DGI Compliance Settings")
