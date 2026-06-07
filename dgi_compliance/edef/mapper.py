"""Map a real ERPNext v16 Sales Invoice to the e-DEF InvoiceRequestDataDto.

IMPORTANT — currency: ALL figures sent to the e-DEF API are in LCY = CDF (company/base currency),
even when the invoice is issued in USD. We send base_* amounts (CDF) for prices/payments and pass
curCode/curRate so the DGI can display an indicative foreign-currency total on its side.

Field references (standard ERPNext, no core change):
  Sales Invoice: name, company, customer, customer_name, tax_id, contact_email, contact_mobile,
                 address_display, currency, conversion_rate, posting_date, posting_time,
                 is_return, return_against, owner
  Sales Invoice Item (.items): item_code, item_name, description, qty, base_rate, base_net_rate,
                 item_tax_template
  Sales Invoice Payment (.payments, POS): mode_of_payment, base_amount
"""
import frappe
from frappe.utils import get_datetime
from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings

INVOICE_TYPES = ("FV", "FT", "FA", "EV", "ET", "EA")
TAX_GROUPS = set("ABCDEFGHIJKLMNOP")


def _seller_nif(doc, settings):
    if settings.default_nif:
        return settings.default_nif
    return frappe.db.get_value("Company", doc.company, "tax_id")


def _seller_isf(doc, settings):
    """Prefer the company's ISF custom field, then the global Settings value."""
    company_isf = frappe.db.get_value("Company", doc.company, "dgi_isf_number")
    return company_isf or settings.isf


def _price_mode(doc, settings):
    for t in (doc.get("taxes") or []):
        if getattr(t, "included_in_print_rate", 0):
            return "ttc"
    return settings.price_mode or "ttc"


def _item_rate_cdf(it, mode):
    # Always CDF (base currency). Use base_net_rate for HT, else base_rate.
    if mode == "ht" and it.get("base_net_rate"):
        return float(it.base_net_rate)
    return float(it.get("base_rate") or it.get("base_net_rate") or 0)


def _invoice_type(doc, settings):
    # Explicit classification on the invoice wins (Link -> DGI Invoice Type, name == code).
    if doc.get("dgi_invoice_type"):
        return doc.get("dgi_invoice_type")
    if doc.get("is_return"):
        return "EA" if doc.get("custom_dgi_export") else "FA"
    if doc.get("custom_dgi_export"):
        return "EV"
    return settings.default_invoice_type or "FV"


def _client(doc):
    name = doc.get("customer_name") or doc.get("customer")
    nif = doc.get("tax_id")
    if not name and not nif:
        return None
    address = frappe.utils.strip_html(doc.get("address_display") or "").replace("\n", ", ").strip(", ").strip()
    return {
        "nif": nif or None,
        "name": name or None,
        "contact": doc.get("contact_email") or doc.get("contact_mobile") or None,
        "address": address or None,
    }


def _payments(doc, settings):
    rows = doc.get("payments") or []
    if not rows:
        return None
    return [{
        "name": settings.payment_type_for(p.mode_of_payment),
        "amount": float(p.base_amount or 0),  # CDF
    } for p in rows]


def _item_edef_type(item_code):
    """Prefer the explicit DGI Item Type on the Item; fall back to the stock-item heuristic."""
    if not item_code:
        return "BIE"
    explicit = frappe.db.get_value("Item", item_code, "dgi_item_type")
    if explicit:
        return explicit
    return "SER" if frappe.db.get_value("Item", item_code, "is_stock_item") == 0 else "BIE"


def _items(doc, settings, mode):
    out = []
    for it in (doc.get("items") or []):
        out.append({
            "code": it.get("item_code") or None,
            "name": it.get("item_name") or it.get("description") or "ARTICLE",
            "type": _item_edef_type(it.get("item_code")),
            "price": _item_rate_cdf(it, mode),
            "quantity": float(it.qty or 0),
            "taxGroup": settings.tax_group_for(it.get("item_tax_template"), None),
        })
    return out


def _currency_block(doc, dto):
    company_ccy = frappe.get_cached_value("Company", doc.company, "default_currency")
    ccy = doc.get("currency")
    # Amounts are already CDF; curCode/curRate let the DGI display the indicative foreign total.
    if ccy and ccy not in (company_ccy, "CDF"):
        dto["curCode"] = ccy
        dto["curRate"] = float(doc.get("conversion_rate") or 0)
        dt = get_datetime(f"{doc.posting_date} {doc.get('posting_time') or '00:00:00'}")
        dto["curDate"] = dt.strftime("%Y-%m-%d %H:%M:%S")


def build_invoice_request(doc):
    settings = get_settings()
    mode = _price_mode(doc, settings)
    dto = {
        "nif": _seller_nif(doc, settings),
        "rn": doc.name,
        "mode": mode,
        "isf": _seller_isf(doc, settings),
        "type": _invoice_type(doc, settings),
        "items": _items(doc, settings, mode),
        "operator": {"id": None, "name": (doc.get("owner") or "SYSTEM")[:60]},
    }
    client = _client(doc)
    if client:
        dto["client"] = client
    payments = _payments(doc, settings)
    if payments:
        dto["payment"] = payments
    if dto["type"] in ("FA", "EA"):
        ref_code = None
        if doc.get("return_against"):
            ref_code = frappe.db.get_value("Sales Invoice", doc.return_against, "custom_dgi_code_def")
        dto["reference"] = doc.get("custom_dgi_reference") or ref_code
        dto["referenceType"] = doc.get("custom_dgi_reference_type")
        dto["referenceDesc"] = doc.get("custom_dgi_reference_desc")
    _currency_block(doc, dto)
    return dto


def validate_invoice_request(dto):
    import re
    errors = []
    if not dto.get("nif"):
        errors.append("nif manquant (Company.tax_id ou Settings.default_nif)")
    if not dto.get("isf") or not re.match(r"^[A-Za-z]{3}-[A-Za-z]{3}-\d{2}$", dto.get("isf") or ""):
        errors.append("isf doit respecter le format AAA-BBB-NN")
    if dto.get("mode") not in ("ttc", "ht"):
        errors.append("mode doit etre 'ttc' ou 'ht'")
    if dto.get("type") not in INVOICE_TYPES:
        errors.append(f"type invalide ({dto.get('type')})")
    items = dto.get("items") or []
    if not items:
        errors.append("items vide")
    for i, it in enumerate(items):
        if not it.get("name"):
            errors.append(f"items[{i}].name manquant")
        if it.get("taxGroup") not in TAX_GROUPS:
            errors.append(f"items[{i}].taxGroup hors A-P")
        if not (it.get("price", -1) >= 0):
            errors.append(f"items[{i}].price invalide")
        if not (it.get("quantity", 0) > 0):
            errors.append(f"items[{i}].quantity doit etre > 0")
    if not (dto.get("operator") or {}).get("name"):
        errors.append("operator.name manquant")
    if dto.get("type") in ("FA", "EA") and not dto.get("reference"):
        errors.append("reference obligatoire pour FA/EA (Code DEF/DGI d'origine)")
    return errors
