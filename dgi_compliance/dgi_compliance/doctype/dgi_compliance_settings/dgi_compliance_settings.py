import frappe
from frappe import _
from frappe.model.document import Document

# Default priorities per mapping source (lower value = evaluated first).
PRIORITY_ITEM_TEMPLATE = 10    # Modele de taxe d'article (ligne)
PRIORITY_SALES_TEMPLATE = 20   # Modele Taxes et Frais de Vente (facture)
PRIORITY_LEGACY_RATE = 90      # legacy tax_rate rows (deprecated)


def _get(d, k):
    """Uniform accessor for dict-like or Document-like objects (None-safe)."""
    if d is None:
        return None
    if isinstance(d, dict):
        return d.get(k)
    if hasattr(d, "get"):
        return d.get(k)
    return getattr(d, k, None)


def effective_priority(row) -> int:
    """Priority of a mapping row; when empty/0, auto-derive from the mapping source."""
    p = _get(row, "priority")
    try:
        p = int(p or 0)
    except (TypeError, ValueError):
        p = 0
    if p > 0:
        return p
    if _get(row, "item_tax_template"):
        return PRIORITY_ITEM_TEMPLATE
    if _get(row, "sales_taxes_template"):
        return PRIORITY_SALES_TEMPLATE
    return PRIORITY_LEGACY_RATE


class DGIComplianceSettings(Document):
    def validate(self):
        # Security: warn (do not block) when the active e-DEF endpoint is not HTTPS.
        url = self.base_url()
        if url and not url.lower().startswith("https://"):
            frappe.msgprint(_("Avertissement securite DGI: l'URL e-DEF active n'est pas en HTTPS "
                              "({0}). Le jeton transiterait en clair.").format(url),
                            indicator="orange", title=_("Securite"))
        self._validate_tax_group_map()

    def _validate_tax_group_map(self):
        """Integrity of the Tax Group Mapping table:
        * each row must reference EXACTLY ONE source (Item Tax Template OR Sales Taxes Template);
          legacy rate-only rows are tolerated but flagged as deprecated;
        * duplicate sources with a different e-DEF group are blocked (ambiguous mapping);
        * empty priorities are auto-assigned (10 item / 20 invoice) so the order is explicit."""
        seen = {}
        for row in (self.tax_group_map or []):
            itt = (row.item_tax_template or "").strip()
            stt = (row.get("sales_taxes_template") or "").strip()
            if itt and stt:
                frappe.throw(
                    _("Tax Group Mapping ligne #{0}: renseignez UN SEUL modele par ligne "
                      "(Modele de taxe d'article OU Modele Taxes et Frais de Vente). "
                      "Creez deux lignes distinctes si les deux doivent etre mappes.").format(row.idx),
                    title=_("Mapping Tax Group"))
            if not itt and not stt:
                if row.tax_rate is None:
                    frappe.throw(
                        _("Tax Group Mapping ligne #{0}: aucune source de mapping. Choisissez un "
                          "Modele de taxe d'article ou un Modele Taxes et Frais de Vente.").format(row.idx),
                        title=_("Mapping Tax Group"))
                frappe.msgprint(
                    _("Tax Group Mapping ligne #{0}: mapping par taux (obsolete). Preferez un "
                      "modele de taxe explicite.").format(row.idx), indicator="orange")
            # auto-assign the priority so the selection order is always visible/explicit
            if not int(row.priority or 0):
                row.priority = effective_priority(row)
            # duplicate detection (same source mapped twice)
            key = ("ITT", itt) if itt else (("STT", stt) if stt else ("RATE", str(row.tax_rate)))
            if key in seen:
                prev = seen[key]
                if prev.edef_tax_group != row.edef_tax_group:
                    frappe.throw(
                        _("Tax Group Mapping: '{0}' est mappe deux fois avec des groupes differents "
                          "(ligne #{1}: {2} / ligne #{3}: {4}). Supprimez le doublon.").format(
                            itt or stt or row.tax_rate, prev.idx, prev.edef_tax_group,
                            row.idx, row.edef_tax_group),
                        title=_("Mapping Tax Group"))
                frappe.msgprint(
                    _("Tax Group Mapping: doublon pour '{0}' (lignes #{1} et #{2}).").format(
                        itt or stt or row.tax_rate, prev.idx, row.idx), indicator="orange")
            else:
                seen[key] = row

    def base_url(self) -> str:
        if (self.environment or "Test") == "Production":
            return (self.base_url_production or "").rstrip("/")
        return (self.base_url_test or "").rstrip("/")

    def get_token(self) -> str:
        # Password fields are decrypted via get_password()
        return self.get_password("token", raise_exception=False) or ""

    def payment_type_for(self, mode_of_payment: str, strict: bool = False) -> str | None:
        """Resolve the e-DEF payment type for an ERPNext Mode of Payment (v3.6).

        Same principle as the Tax Group: an existing mapping ALWAYS wins, and the
        Default Payment Type applies ONLY when NO Mode of Payment is specified on
        the payment row. strict=True: a specified-but-unmapped mode returns None so
        the caller can raise an explicit error instead of silently using the default.
        """
        if mode_of_payment:
            for row in (self.payment_mode_map or []):
                if row.erpnext_mode_of_payment == mode_of_payment:
                    return row.edef_payment_type
            if strict:
                return None
        return self.default_payment_type or "ESPECES"

    # ---- Tax Group resolution (v3.5: priority-driven, single table) ----

    def tax_group_rows(self):
        """Mapping rows sorted by (effective priority, table order). Lower priority wins."""
        rows = list(self.tax_group_map or [])
        rows.sort(key=lambda r: (effective_priority(r), int(_get(r, "idx") or 0)))
        return rows

    def tax_group_for(self, item_tax_template: str | None, rate: float | None) -> str:
        """Legacy resolution (line template / rate only). Prefer tax_group_for_invoice()."""
        for row in self.tax_group_rows():
            if item_tax_template and row.item_tax_template == item_tax_template:
                return row.edef_tax_group
        if rate is not None:
            for row in self.tax_group_rows():
                if not row.item_tax_template and row.tax_rate is not None \
                        and abs(float(row.tax_rate) - float(rate)) < 1e-6:
                    return row.edef_tax_group
        return self.default_tax_group or "A"

    def tax_group_for_invoice(self, doc, item=None, strict: bool = False) -> str | None:
        """Resolve the e-DEF Tax Group (A-P) for an invoice line.

        v3.5: the resolution is driven by the Priority column of the single mapping table
        (lower value evaluated first). Default priorities keep the documented order:

          1. (prio 10) Item Tax Template of the LINE          -> mapped e-DEF Tax Group;
          2. (prio 20) Sales Taxes and Charges Template of the
                       INVOICE (taxes_and_charges)            -> mapped e-DEF Tax Group;
          3. Default Tax Group ONLY when NO tax is specified on the invoice
             (no line template, no invoice template, no tax rows).

        An existing mapping ALWAYS wins over the default group.
        strict=True: when a tax IS specified but has no mapping, return None so the
        caller can raise an explicit error instead of silently sending the default.
        """
        itt = _get(item, "item_tax_template")
        stt = _get(doc, "taxes_and_charges")

        # Walk the mapping rows in priority order; first row whose source matches wins.
        for row in self.tax_group_rows():
            r_itt = _get(row, "item_tax_template")
            r_stt = _get(row, "sales_taxes_template")
            if r_itt and itt and r_itt == itt:
                return _get(row, "edef_tax_group")
            if r_stt and stt and r_stt == stt:
                return _get(row, "edef_tax_group")

        # Default group only when no tax is specified at all.
        has_tax_rows = bool(_get(doc, "taxes") or [])
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


@frappe.whitelist()
def check_tax_group_mapping() -> dict:
    """Diagnostic ('Verifier le mapping taxes' button): list the ACTIVE tax templates that have
    no Tax Group mapping. Sending an invoice using one of them would block normalization."""
    frappe.only_for(["System Manager", "Accounts Manager"])
    settings = get_settings()
    mapped_itt = {r.item_tax_template for r in (settings.tax_group_map or []) if r.item_tax_template}
    mapped_stt = {r.get("sales_taxes_template") for r in (settings.tax_group_map or [])
                  if r.get("sales_taxes_template")}
    all_itt = frappe.get_all("Item Tax Template", filters={"disabled": 0}, pluck="name")
    all_stt = frappe.get_all("Sales Taxes and Charges Template", filters={"disabled": 0}, pluck="name")
    return {
        "unmapped_item_tax_templates": sorted(set(all_itt) - mapped_itt),
        "unmapped_sales_taxes_templates": sorted(set(all_stt) - mapped_stt),
        "mapped_count": len(mapped_itt) + len(mapped_stt),
        "default_tax_group": settings.default_tax_group or "A",
    }
