"""Helpers exposed to Jinja Print Formats (normalized DGI invoice).

Registered via the `jinja` hook so they can be called directly in a Print Format, e.g.:
    {% set spec = dgi_tax_summary(doc) %}
    {{ dgi_isf(doc) }}
All amounts are returned as positive magnitudes (credit notes are negative in ERPNext).
"""
import frappe
from frappe.utils import flt, money_in_words
from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings


def dgi_isf(doc=None) -> str:
    """Seller ISF - single source of truth in DGI Compliance Settings."""
    try:
        return (get_settings().isf or "").strip()
    except Exception:
        return ""


def dgi_pos_nid(doc) -> str:
    """DEF NID of the DGI Point of Sale linked to the invoice's POS Profile (if any)."""
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


def dgi_amount_in_words(doc) -> str:
    """Grand total (absolute, base currency CDF) spelled out, for the normalized invoice."""
    try:
        amount = abs(flt(doc.base_grand_total) or flt(doc.grand_total))
        ccy = frappe.get_cached_value("Company", doc.company, "default_currency") or doc.currency
        return money_in_words(amount, ccy)
    except Exception:
        return ""


def dgi_tax_summary(doc):
    """'Montant TVA Specification' rows grouped by DGI tax group (A-P).

    Returns a list of {group, rate, base, vat} (positive magnitudes). Base = sum of items'
    net amount (CDF) per resolved DGI tax group; VAT = base * group rate %. The group rate is
    read from DGI Tax Group (synced from the e-MCF), falling back to 0 when unknown."""
    try:
        settings = get_settings()
    except Exception:
        return []
    groups = {}
    for it in (doc.get("items") or []):
        tg = settings.tax_group_for(it.get("item_tax_template"), None) or (settings.default_tax_group or "A")
        base = abs(flt(it.get("base_net_amount")))
        g = groups.setdefault(tg, {"group": tg, "base": 0.0, "rate": 0.0, "vat": 0.0})
        g["base"] += base
    for tg, g in groups.items():
        rate = 0.0
        if frappe.db.exists("DGI Tax Group", tg):
            rate = flt(frappe.db.get_value("DGI Tax Group", tg, "rate"))
        g["rate"] = rate
        g["vat"] = flt(g["base"] * rate / 100.0)
    return [groups[k] for k in sorted(groups)]


def dgi_item_type(item_code) -> str:
    """DGI item type (BIE/SER/TAX) for an item: explicit on the Item, else stock heuristic."""
    if not item_code:
        return "BIE"
    explicit = frappe.db.get_value("Item", item_code, "dgi_item_type")
    if explicit:
        return explicit
    return "SER" if frappe.db.get_value("Item", item_code, "is_stock_item") == 0 else "BIE"


def dgi_item_tax_group(item_tax_template=None) -> str:
    """DGI tax group (A-P) resolved for an item tax template (or the default)."""
    try:
        return get_settings().tax_group_for(item_tax_template, None)
    except Exception:
        return ""


def dgi_invoice_lines(doc):
    """Pre-computed, positive-magnitude invoice lines for the normalized print format.
    Returns dicts: type, code, name, qty, uom, pu_ht, montant_ht, remise, group, rate, net_ht."""
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
        out.append({
            "type": dgi_item_type(it.get("item_code")),
            "code": it.get("item_code") or "",
            "name": it.get("item_name") or it.get("description") or "",
            "qty": qty,
            "uom": it.get("uom") or "",
            "pu_ht": abs(flt(it.get("net_rate"))),
            "montant_ht": abs(flt(it.get("net_amount"))),
            "remise": abs(flt(it.get("discount_amount"))) * qty,
            "group": grp,
            "rate": rate,
            "net_ht": abs(flt(it.get("net_amount"))),
        })
    return out


def dgi_totals(doc):
    """Positive-magnitude document totals for the normalized print format."""
    return {
        "net_total": abs(flt(doc.get("net_total"))),
        "discount": abs(flt(doc.get("discount_amount"))),
        "tax_total": abs(flt(doc.get("total_taxes_and_charges"))),
        "grand_total": abs(flt(doc.get("grand_total"))),
        "base_grand_total": abs(flt(doc.get("base_grand_total"))),
    }
