"""Helpers exposed to Jinja Print Formats (normalized DGI invoice).

Registered via the `jinja` hook. All amounts are positive magnitudes (credit notes are negative
in ERPNext). Line/total amounts are in the INVOICE currency; *_cdf values are in CDF (the values
transmitted to the DGI). Discounts: gross = price_list_rate x qty, remise = gross - net.
"""
import frappe
from frappe.utils import flt, money_in_words
from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings

INVOICE_TYPE_LABELS = {
    "FV": "Facture de vente",
    "EV": "Facture de vente a l'export",
    "FT": "Facture d'acompte",
    "ET": "Facture d'acompte a l'export",
    "FA": "Facture d'avoir",
    "EA": "Facture d'avoir a l'export",
}


def dgi_type_label(code) -> str:
    """French header label for a DGI invoice type code."""
    return INVOICE_TYPE_LABELS.get((code or "").upper(), "Facture normalisee")


def dgi_isf(doc=None) -> str:
    try:
        return (get_settings().isf or "").strip()
    except Exception:
        return ""


def dgi_pos_nid(doc) -> str:
    try:
        pos_profile = doc.get("pos_profile")
        if not pos_profile:
            return ""
        dgi_pos = frappe.db.get_value("POS Profile", pos_profile, "dgi_point_of_sale")
        if not dgi_pos:
            return ""
        return frappe.db.get_value("DGI Point of Sale", dgi_pos, "nid") or ""
    except Exception:
        return ""


def dgi_client_registration(doc) -> str:
    """Customer legal registration number (RCCM / Id. Nat.)."""
    try:
        if not doc.get("customer"):
            return ""
        return frappe.db.get_value("Customer", doc.get("customer"), "dgi_registration_no") or ""
    except Exception:
        return ""


def dgi_print_label(doc) -> str:
    """ORIGINAL for the first print, DUPLICATA afterwards."""
    try:
        n = int(doc.get("custom_dgi_print_count") or 0)
    except Exception:
        n = 0
    return "DUPLICATA" if n >= 2 else "ORIGINAL"


def dgi_amount_in_words(doc) -> str:
    try:
        amount = abs(flt(doc.base_grand_total) or flt(doc.grand_total))
        ccy = frappe.get_cached_value("Company", doc.company, "default_currency") or doc.currency
        return money_in_words(amount, ccy)
    except Exception:
        return ""


def dgi_is_foreign(doc) -> bool:
    try:
        company_ccy = frappe.get_cached_value("Company", doc.company, "default_currency")
    except Exception:
        company_ccy = "CDF"
    ccy = doc.get("currency")
    return bool(ccy and ccy not in (company_ccy, "CDF"))


def dgi_cur_rate(doc):
    try:
        from dgi_compliance.edef.mapper import _cur_rate
        return _cur_rate(doc)
    except Exception:
        return flt(doc.get("conversion_rate"))


def dgi_item_type(item_code) -> str:
    if not item_code:
        return "BIE"
    explicit = frappe.db.get_value("Item", item_code, "dgi_item_type")
    if explicit:
        return explicit
    return "SER" if frappe.db.get_value("Item", item_code, "is_stock_item") == 0 else "BIE"


def dgi_item_tax_group(item_tax_template=None) -> str:
    try:
        return get_settings().tax_group_for(item_tax_template, None)
    except Exception:
        return ""


def _line_gross_unit(it):
    """Gross unit price (before discount), invoice currency. Falls back to rate."""
    plr = abs(flt(it.get("price_list_rate")))
    return plr if plr else abs(flt(it.get("rate")))


def dgi_invoice_lines(doc):
    """Invoice lines for the print format (invoice currency, positive magnitudes).
    Columns: type, code, name, qty, uom, pu_ht (gross unit), montant_ht (gross), remise,
    group, rate, net_ht (net amount)."""
    try:
        settings = get_settings()
    except Exception:
        settings = None
    out = []
    for it in (doc.get("items") or []):
        grp = ""
        if settings:
            try:
                grp = settings.tax_group_for(it.get("item_tax_template"), None) or ""
            except Exception:
                grp = ""
        rate = None
        if grp and frappe.db.exists("DGI Tax Group", grp):
            rate = flt(frappe.db.get_value("DGI Tax Group", grp, "rate"))
        qty = abs(flt(it.get("qty")))
        gross_unit = _line_gross_unit(it)
        gross_amount = gross_unit * qty
        net_amount = abs(flt(it.get("net_amount")))
        remise = gross_amount - net_amount
        if remise < 0:
            remise = 0.0
        out.append({
            "type": dgi_item_type(it.get("item_code")),
            "code": it.get("item_code") or "",
            "name": it.get("item_name") or it.get("description") or "",
            "qty": qty,
            "uom": it.get("uom") or "",
            "pu_ht": gross_unit,
            "montant_ht": gross_amount,
            "remise": remise,
            "group": grp,
            "rate": rate,
            "net_ht": net_amount,
        })
    return out


def _gross_total(doc, base=False):
    total = 0.0
    for it in (doc.get("items") or []):
        qty = abs(flt(it.get("qty")))
        if base:
            unit = abs(flt(it.get("base_price_list_rate"))) or abs(flt(it.get("base_rate")))
        else:
            unit = _line_gross_unit(it)
        total += unit * qty
    return total


def dgi_totals(doc):
    """Totals block: montant_facture (HT brut), remise, tva, net_a_payer (TTC).
    Net a payer = base_grand_total (authoritative). Remise = Montant facture - (Net a payer - TVA),
    which captures both line and document-level discounts. *_cdf = CDF values sent to the DGI."""
    tva = abs(flt(doc.get("total_taxes_and_charges")))
    net_a_payer = abs(flt(doc.get("grand_total")))
    taxable_net = net_a_payer - tva
    montant_facture = _gross_total(doc, base=False)
    if montant_facture < taxable_net:
        montant_facture = taxable_net  # safety (no negative discount)
    remise = montant_facture - taxable_net

    tva_cdf = abs(flt(doc.get("base_total_taxes_and_charges")))
    net_a_payer_cdf = abs(flt(doc.get("base_grand_total")))
    taxable_net_cdf = net_a_payer_cdf - tva_cdf
    montant_facture_cdf = _gross_total(doc, base=True)
    if montant_facture_cdf < taxable_net_cdf:
        montant_facture_cdf = taxable_net_cdf
    remise_cdf = montant_facture_cdf - taxable_net_cdf

    return {
        "montant_facture": montant_facture, "remise": remise, "tva": tva, "net_a_payer": net_a_payer,
        "montant_facture_cdf": montant_facture_cdf, "remise_cdf": remise_cdf,
        "tva_cdf": tva_cdf, "net_a_payer_cdf": net_a_payer_cdf,
    }


def dgi_tax_summary(doc):
    """'Montant TVA Specification' grouped by DGI tax group (A-P), in INVOICE currency.
    base = sum of items' net amount per group; vat = base * group rate %."""
    try:
        settings = get_settings()
    except Exception:
        return []
    groups = {}
    for it in (doc.get("items") or []):
        tg = settings.tax_group_for(it.get("item_tax_template"), None) or (settings.default_tax_group or "A")
        base = abs(flt(it.get("net_amount")))
        g = groups.setdefault(tg, {"group": tg, "base": 0.0, "rate": 0.0, "vat": 0.0})
        g["base"] += base
    for tg, g in groups.items():
        rate = 0.0
        if frappe.db.exists("DGI Tax Group", tg):
            rate = flt(frappe.db.get_value("DGI Tax Group", tg, "rate"))
        g["rate"] = rate
        g["vat"] = flt(g["base"] * rate / 100.0)
    return [groups[k] for k in sorted(groups)]
