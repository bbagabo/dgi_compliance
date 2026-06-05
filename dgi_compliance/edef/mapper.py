"""Map a real ERPNext v16 Sales Invoice to the e-DEF InvoiceRequestDataDto.

Field references are the standard ERPNext schema (no core change required):
  Sales Invoice: name, company, customer, customer_name, tax_id, contact_email,
                 contact_mobile, address_display, currency, conversion_rate,
                 posting_date, posting_time, is_return, return_against, is_pos, owner
  Sales Invoice Item (child .items): item_code, item_name, description, qty, rate,
                 net_rate, item_tax_template, uom
  Sales Invoice Payment (child .payments, POS only): mode_of_payment, amount
"""
import frappe
from frappe.utils import get_datetime, getdate
from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings

INVOICE_TYPES = ("FV", "FT", "FA", "EV", "ET", "EA")
TAX_GROUPS = set("ABCDEFGHIJKLMNOP")


def _seller_nif(doc, settings) -> str | None:
    if settings.default_nif:
        return settings.default_nif
    return frappe.db.get_value("Company", doc.company, "tax_id")


def _price_mode(doc, settings) -> str:
    # 'ttc' if any tax is inclusive in the unit price, else 'ht'; fall back to setting.
    for t in (doc.get("taxes") or []):
        if getattr(t, "included_in_print_rate", 0):
            return "ttc"
    return settings.price_mode or "ttc"


def _item_rate(it, mode: str) -> float:
    # When prices are HT, ERPNext's net_rate is the pre-tax unit price; otherwise use rate.
    if mode == "ht" and it.get("net_rate"):
        return float(it.net_rate)
    return float(it.rate or 0)


def _invoice_type(doc, settings) -> str:
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
    address = doc.get("address_display") or ""
    # address_display is HTML; flatten to a single line
    address = frappe.utils.strip_html(address).replace("\n", ", ").strip(", ").strip()
    return {
        "nif": nif or None,
        "name": name or None,
        "contact": doc.get("contact_email") or doc.get("contact_mobile") or None,
        "address": address or None,
    }


def _payments(doc, settings):
    rows = doc.get("payments") or []
    if not rows:
        return None  # e-MCF defaults to ESPECES
    out = []
    for p in rows:
        out.append({
            "name": settings.payment_type_for(p.mode_of_payment),
            "amount": float(p.amount or 0),
        })
    return out


def _items(doc, settings, mode):
    out = []
    for it in (doc.get("items") or []):
        rate_for_lookup = None
        out.append({
            "code": it.get("item_code") or None,
            "name": it.get("item_name") or it.get("description") or "ARTICLE",
            "type": "SER" if frappe.db.get_value("Item", it.get("item_code"), "is_stock_item") == 0 else "BIE",
            "price": _item_rate(it, mode),
            "quantity": float(it.qty or 0),
            "taxGroup": settings.tax_group_for(it.get("item_tax_template"), rate_for_lookup),
        })
    return out


def _currency_block(doc, dto):
    company_ccy = frappe.get_cached_value("Company", doc.company, "default_currency")
    ccy = doc.get("currency")
    if ccy and ccy not in (company_ccy, "CDF"):
        dto["curCode"] = ccy
        dto["curRate"] = float(doc.get("conversion_rate") or 0)
        dt = get_datetime(f"{doc.posting_date} {doc.get('posting_time') or '00:00:00'}")
        dto["curDate"] = dt.strftime("%Y-%m-%d %H:%M:%S")


def build_invoice_request(doc) -> dict:
    settings = get_settings()
    mode = _price_mode(doc, settings)
    dto = {
        "nif": _seller_nif(doc, settings),
        "rn": doc.name,
        "mode": mode,
        "isf": settings.isf,
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
    # Credit note (avoir): reference = Code DEF/DGI of the original invoice (24 chars)
    if dto["type"] in ("FA", "EA"):
        ref_code = None
        if doc.get("return_against"):
            ref_code = frappe.db.get_value("Sales Invoice", doc.return_against, "custom_dgi_code_def")
        dto["reference"] = doc.get("custom_dgi_reference") or ref_code
        dto["referenceType"] = doc.get("custom_dgi_reference_type")
        dto["referenceDesc"] = doc.get("custom_dgi_reference_desc")
    _currency_block(doc, dto)
    return dto


def validate_invoice_request(dto: dict) -> list[str]:
    import re
    errors = []
    if not dto.get("nif"):
        errors.append("nif manquant (Company.tax_id ou Settings.default_nif)")
    if not dto.get("isf") or not re.match(r"^[A-Za-z]{3}-[A-Za-z]{3}-\d{2}$", dto.get("isf") or ""):
        errors.append("isf doit respecter le format AAA-BBB-NN")
    if dto.get("mode") not in ("ttc", "ht"):
        errors.append("mode doit être 'ttc' ou 'ht'")
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
            errors.append(f"items[{i}].quantity doit être > 0")
    if not (dto.get("operator") or {}).get("name"):
        errors.append("operator.name manquant")
    if dto.get("type") in ("FA", "EA") and not dto.get("reference"):
        errors.append("reference obligatoire pour FA/EA (Code DEF/DGI d'origine)")
    return errors
