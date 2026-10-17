"""Build the payload consumed by the local MCF agent (physical fiscal device, v4.0).

The payload is a pure-JSON, protocol-agnostic description of the invoice: the agent
turns it into MCF-SFE serial commands (C3h, C0h, 31h, 36h, 33h, 35h, 38h). Amounts are
in CDF (the MCF records CDF; for USD invoices the exchange rate CRT/CDT is passed to C0h).

Reuses the e-DEF mapper so BOTH modes share exactly the same business rules:
strict tax-group + payment-type mappings, discounts (originalPrice), credit notes
(FA/EA + nature RR + reference RN), currency matrix, comments A-H.
"""
import frappe
from frappe import _
from frappe.utils import flt
from dgi_compliance.edef.mapper import build_invoice_request, validate_invoice_request
from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings

# e-DEF payment type -> MCF 35h letter (E espece, V virement, C carte, M mobile, D cheque, A autre)
PAYMENT_LETTERS = {
    "ESPECES": "E", "VIREMENT": "V", "CARTEBANCAIRE": "C",
    "MOBILEMONEY": "M", "CHEQUES": "D", "CREDIT": "A", "AUTRE": "A",
}
COMMENT_LETTERS = "abcdefgh"


class MCFPayloadError(frappe.ValidationError):
    """The invoice cannot be expressed as a physical-MCF job."""


def _tax_rate_for_group(letter):
    return flt(frappe.db.get_value("DGI Tax Group", letter, "rate") or 0)


def compute_totals(items, mode) -> dict:
    """Mirror the MCF arithmetic: per-group amounts, VAT per group, then MV/MT.
    TTC mode: VAT extracted (mon * r / (100+r)); HT mode: VAT added (mon * r / 100)."""
    groups = {}
    for row in items:
        g = groups.setdefault(row["tax_group"], {"rate": flt(row["tax_rate"]), "mon": 0.0})
        g["mon"] = round(g["mon"] + flt(row["amount"]), 2)
    mv = mt = 0.0
    for g in groups.values():
        r = g["rate"]
        if (mode or "TTC").upper() == "TTC":
            vat = round(g["mon"] * r / (100.0 + r), 2) if r else 0.0
            mv = round(mv + g["mon"], 2)
        else:
            vat = round(g["mon"] * r / 100.0, 2) if r else 0.0
            mv = round(mv + g["mon"] + vat, 2)
        mt = round(mt + vat, 2)
    return {"mv": mv, "mt": mt}


def build_mcf_payload(doc, settings=None) -> dict:
    settings = settings or get_settings()
    dto = build_invoice_request(doc)          # strict mappings (tax groups, payment types)
    errors = validate_invoice_request(dto)
    if errors:
        frappe.throw(_("Payload MCF invalide - ") + "; ".join(errors),
                     exc=MCFPayloadError, title=_("MCF Physique"))

    cur = dto.get("curCode")
    if cur and cur != "USD":
        frappe.throw(_("Le MCF physique ne supporte que le taux de change USD (commande C0h). "
                       "Devise de la facture: {0}. Facturez en CDF ou USD, ou repassez en mode "
                       "E-MCF (API).").format(cur),
                     exc=MCFPayloadError, title=_("MCF Physique"))

    mode = (dto.get("mode") or "ttc").upper()  # TTC | HT
    items = []
    for src in dto["items"]:
        letter = src["taxGroup"]
        qty = flt(src.get("quantity") or 1)
        price = flt(src.get("price") or 0)
        row = {
            "name": (src.get("name") or "ARTICLE")[:64],
            "desc": (src.get("code") or None),
            "itype": src.get("type") or "BIE",
            "tax_group": letter,
            "tax_rate": _tax_rate_for_group(letter),
            "amount": round(price * qty, 2),
            "price": price,
            "qty": qty,
        }
        if src.get("originalPrice"):
            row["orig_price"] = flt(src["originalPrice"])
            row["price_desc"] = (src.get("priceModification") or "Remise")[:24]
        items.append(row)

    totals = compute_totals(items, mode)
    totals["erp_total_cdf"] = abs(flt(doc.get("base_grand_total") or 0))

    client = dto.get("client") or {}
    crccm = ""
    if doc.get("customer"):
        crccm = frappe.db.get_value("Customer", doc.get("customer"), "dgi_registration_no") or ""

    payload = {
        "version": 1,
        "invoice": doc.name,
        "fn": doc.name[:64],
        "nif": dto.get("nif") or "",
        "isf": dto.get("isf") or "",
        "pmode": mode,
        "invoice_type": dto.get("type"),
        "operator": {
            "id": ((doc.get("owner") or "SFE").split("@")[0])[:16],
            "name": (frappe.db.get_value("User", doc.get("owner"), "full_name")
                     or doc.get("owner") or "SFE")[:32],
        },
        "client": {
            "ctype": client.get("type") or "PP",
            "cnif": client.get("nif") or "",
            "cnom": client.get("name") or "",
            "cadd": client.get("address") or "",
            "ctel": doc.get("contact_mobile") or "",
            "ceml": doc.get("contact_email") or "",
            "crccm": crccm,
        },
        "items": items,
        "payments": [
            {"letter": PAYMENT_LETTERS.get((p.get("name") or "").upper(), "A"),
             "amount": flt(p.get("amount") or 0)}
            for p in (dto.get("payment") or [])
        ],
        "comments": {l.upper(): dto.get("cmt" + l) for l in COMMENT_LETTERS if dto.get("cmt" + l)},
        "totals": totals,
    }
    if dto.get("type") in ("FA", "EA"):
        payload["credit_note"] = {"rr": dto.get("referenceType") or "COR",
                                  "rn": dto.get("reference") or ""}
    if cur == "USD":
        payload["exchange"] = {"crt": flt(dto.get("curRate") or 0),
                               "cdt": str(doc.get("posting_date") or "").replace("-", "")[:8]}
    return payload
