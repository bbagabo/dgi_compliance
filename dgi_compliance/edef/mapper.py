"""Map a real ERPNext v16 Sales Invoice to the e-MCF InvoiceRequestDataDto (API e-MCF v1.3).

Currency: ALL figures sent to the e-MCF are in CDF (company base currency, base_* fields),
even for a USD invoice; curCode/curRate/curDate let the DGI display the indicative
foreign-currency total.

v5.0.0 (HL1):
  * price mode derived from the invoice (ttc only when the taxes are included in the price);
  * unit prices built from the NET line amount, so line AND document-level discounts are
    reflected in both modes; prices rounded to 2 decimals, quantities to 3 (SFE 1.5.1/1.5.2);
  * specific tax (taxSpecificValue / taxSpecificAmount) carried inside the line net amount;
  * tax groups A-Z (FACNO groupes de taxation v2.0), item flagged "hors champ" sent with the
    code "HORS CHAMP" (Communique officiel n°010);
  * payments from edef.payments (same lines as the printed invoice), PaymentDto
    currencyCode/currencyRate;
  * operator = the user who normalizes (full name + user id);
  * credit notes: reference forced to the original Code DEF/DGI, or "RRR" (SFE 1.1.26/1.1.28).
"""
import frappe
from frappe import _
from frappe.utils import flt, get_datetime

from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings
from dgi_compliance.edef.calc import (
    VAT_ONLY_GROUPS, round2, round3, split_specific_tax, parse_specific_tax,
)
from dgi_compliance.edef.payments import PaymentTypeMappingError, resolve_payment_lines

__all__ = ["PaymentTypeMappingError"]

INVOICE_TYPES = ("FV", "FT", "FA", "EV", "ET", "EA")
CREDIT_TYPES = ("FA", "EA")
TAX_GROUPS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
CLIENT_TYPES = ("PP", "PM", "PC", "PL", "AO")
REFERENCE_TYPES = ("COR", "RAN", "RAM", "RRR")
REFERENCE_LABELS = {"COR": "Correction", "RAN": "Annulation", "RAM": "Avoir suite reprise",
                    "RRR": "RRR"}
COMMENT_LETTERS = "abcdefgh"  # cmta..cmth = Commentaire A..H
HORS_CHAMP_CODE = "HORS CHAMP"


class TaxGroupMappingError(frappe.ValidationError):
    """A tax is specified on the invoice but has no Tax Group mapping."""


# --------------------------------------------------------------------------- #
# Header data
# --------------------------------------------------------------------------- #

def _seller_nif(doc, settings):
    if settings.default_nif:
        return settings.default_nif.strip()
    return (frappe.db.get_value("Company", doc.company, "tax_id") or "").strip() or None


def _seller_isf(doc, settings):
    """ISF is defined ONLY in DGI Compliance Settings (single source of truth)."""
    return (settings.isf or "").strip() or None


def price_mode(doc, settings=None):
    """'ttc' when at least one tax is included in the printed rate, 'ht' when the invoice
    carries taxes added on top of the price; Settings.price_mode only when no tax at all."""
    settings = settings or get_settings()
    taxes = doc.get("taxes") or []
    if any(getattr(t, "included_in_print_rate", 0) or (t.get("included_in_print_rate") if hasattr(t, "get") else 0)
           for t in taxes):
        return "ttc"
    if taxes:
        return "ht"
    return (settings.price_mode or "ttc").lower()


# kept for backward compatibility (v4 name)
_price_mode = price_mode


def _invoice_type(doc, settings):
    """Explicit "Type de facture DGI" wins; returns -> FA/EA; export -> EV; else default FV."""
    if doc.get("dgi_invoice_type"):
        return doc.get("dgi_invoice_type")
    if doc.get("is_return"):
        return "EA" if doc.get("custom_dgi_export") else "FA"
    if doc.get("custom_dgi_export"):
        return "EV"
    return settings.default_invoice_type or "FV"


def _client_dgi_type(doc, settings):
    ctype = doc.get("dgi_customer_type")
    if not ctype and doc.get("customer"):
        ctype = frappe.db.get_value("Customer", doc.get("customer"), "dgi_customer_type")
    if not ctype and doc.get("customer"):
        native = frappe.db.get_value("Customer", doc.get("customer"), "customer_type")
        ctype = settings.default_dgi_type_for_customer_type(native)
    return ctype or None


def _client(doc, settings):
    name = doc.get("customer_name") or doc.get("customer")
    nif = (doc.get("tax_id") or "").strip()
    address = frappe.utils.strip_html(doc.get("address_display") or "").replace("\n", ", ").strip(", ").strip()
    client = {
        "nif": nif or None,
        "name": name or None,
        "contact": doc.get("contact_mobile") or doc.get("contact_email") or None,
        "address": address or None,
    }
    ctype = _client_dgi_type(doc, settings)
    if ctype:
        client["type"] = ctype
        desc = frappe.db.get_value("DGI Customer Type", ctype, "description") \
            if frappe.db.exists("DGI Customer Type", ctype) else None
        if desc:
            client["typeDesc"] = desc
    client = {k: v for k, v in client.items() if v not in (None, "")}
    return client or None


def operator_for(doc):
    """The user performing the normalization (scheduler/Guest falls back to the creator)."""
    user = getattr(getattr(frappe, "session", None), "user", None)
    if not user or user == "Guest":
        user = doc.get("owner") or "Administrator"
    full_name = frappe.utils.get_fullname(user) or user
    return {"id": user[:60], "name": full_name[:60]}


# --------------------------------------------------------------------------- #
# Lines
# --------------------------------------------------------------------------- #

def group_rate(letter):
    if not letter or not frappe.db.exists("DGI Tax Group", letter):
        return 0.0
    return flt(frappe.db.get_value("DGI Tax Group", letter, "rate"))


def _item_edef_type(item_code):
    if not item_code:
        return "BIE"
    explicit = frappe.db.get_value("Item", item_code, "dgi_item_type")
    if explicit:
        return explicit
    return "SER" if frappe.db.get_value("Item", item_code, "is_stock_item") == 0 else "BIE"


def _is_hors_champ(item_code):
    if not item_code:
        return False
    try:
        return bool(frappe.db.get_value("Item", item_code, "dgi_hors_champ"))
    except Exception:
        return False


def _resolve_tax_group_strict(doc, it, settings):
    grp = settings.tax_group_for_invoice(doc, it, strict=True)
    if grp:
        return grp
    tpl = it.get("item_tax_template") or doc.get("taxes_and_charges") or "les taxes de la facture"
    frappe.throw(
        _("Mapping Tax Group manquant pour '{0}' (ligne #{1}). Ajoutez la correspondance dans "
          "DGI Compliance Settings > Tax Group Mapping (Modele Taxes et Frais de Vente ou "
          "Modele de taxe d'article -> groupe de taxation). Le Default Tax Group ne s'applique "
          "que lorsqu'aucune taxe n'est specifiee sur la facture.").format(tpl, it.get("idx") or "?"),
        exc=TaxGroupMappingError, title=_("Mapping Tax Group DGI"))


def build_lines(doc, settings=None, mode=None):
    """Per-line fiscal data in CDF, shared by the e-MCF request and the printed invoice."""
    settings = settings or get_settings()
    mode = mode or price_mode(doc, settings)
    doc_discount = bool(flt(doc.get("discount_amount")))
    lines = []
    for it in (doc.get("items") or []):
        qty = abs(flt(it.get("qty")))
        grp = _resolve_tax_group_strict(doc, it, settings)
        r = group_rate(grp)
        net = abs(flt(it.get("base_net_amount")))                 # CDF, excl. VAT, after discounts
        try:
            net_wo_ts, ts_amount, ts_value = split_specific_tax(net, qty, it.get("dgi_specific_tax"))
        except ValueError:
            frappe.throw(_("Ligne #{0}: valeur de taxe specifique invalide '{1}' (attendu: montant "
                           "par unite ou pourcentage, ex. 230 ou 10%).").format(it.get("idx"), it.get("dgi_specific_tax")))
        vat_factor = 1.0 if (grp in VAT_ONLY_GROUPS or not r) else (1 + r / 100.0)

        if qty:
            if mode == "ttc":
                entered = abs(flt(it.get("base_rate")))
                if (not doc_discount and not ts_amount and vat_factor != 1.0
                        and abs(entered * qty - net * vat_factor) <= 0.01 * qty + 0.02):
                    unit = entered                                # TTC price exactly as entered
                else:
                    unit = net_wo_ts * vat_factor / qty
            else:
                unit = net_wo_ts / qty
        else:
            unit = 0.0
        price = round2(unit)

        # Price before any discount (originalPrice), same basis as `price`.
        gross = abs(flt(it.get("base_price_list_rate"))) or abs(flt(it.get("base_rate")))
        if ts_amount and qty:
            kind, number = parse_specific_tax(it.get("dgi_specific_tax"))
            ts_unit = ts_amount / qty
            if mode == "ttc":
                gross = gross - ts_unit * vat_factor
            else:
                gross = gross / (1 + number / 100.0) if kind == "percent" else gross - ts_unit
        gross = round2(gross)

        disc_pct = flt(it.get("discount_percentage"))
        line = {
            "idx": it.get("idx"),
            "item_code": it.get("item_code") or "",
            "code": HORS_CHAMP_CODE if _is_hors_champ(it.get("item_code")) else (it.get("item_code") or None),
            "name": (it.get("item_name") or it.get("description") or "ARTICLE").strip(),
            "type": _item_edef_type(it.get("item_code")),
            "group": grp,
            "rate": r,
            "price": price,
            "quantity": round3(qty),
            "amount": round2(price * round3(qty)),
            "uom": it.get("uom") or "",
            "ts_value": ts_value,
            "ts_amount": round2(ts_amount),
            "gross": gross,
            "discount_label": None,
        }
        if gross > price + 0.005:
            line["discount_label"] = ("Remise {0}%".format("%g" % disc_pct) if disc_pct > 0
                                      else ("Remise globale" if doc_discount else "Remise"))
        lines.append(line)
    return lines


def _items(doc, settings, mode):
    send_disc = bool(getattr(settings, "send_line_discounts", 1))
    out = []
    for ln in build_lines(doc, settings, mode):
        row = {
            "code": ln["code"],
            "name": ln["name"],
            "type": ln["type"],
            "price": ln["price"],
            "quantity": ln["quantity"],
            "taxGroup": ln["group"],
        }
        if ln["ts_amount"]:
            row["taxSpecificValue"] = ln["ts_value"]
            row["taxSpecificAmount"] = ln["ts_amount"]
        if send_disc and ln["discount_label"]:
            row["originalPrice"] = ln["gross"]
            row["priceModification"] = ln["discount_label"]
        out.append(row)
    return out


# --------------------------------------------------------------------------- #
# Currency
# --------------------------------------------------------------------------- #

def _foreign_ccy(doc):
    company_ccy = frappe.get_cached_value("Company", doc.company, "default_currency")
    ccy = doc.get("currency")
    if ccy and ccy not in (company_ccy, "CDF"):
        return ccy
    return None


def _cur_rate(doc):
    """CDF per 1 unit of the foreign currency (currency rule, then the invoice rate)."""
    ccy = doc.get("currency")
    settings = get_settings()
    rule = settings.currency_rule_for(ccy, _invoice_type(doc, settings))
    if rule:
        src = (rule.get("rate_source") or "ERPNext")
        if src == "Manuel" and flt(rule.get("manual_rate")) > 0:
            return flt(rule.get("manual_rate"))
        if src in ("DGI Officiel", "DGI Official"):
            off = frappe.db.get_value("DGI Reference Value", f"Currency Rate::{ccy}", "value")
            if off:
                return flt(off)
    rate = flt(doc.get("conversion_rate"))
    if rate > 0:
        return rate
    gt = abs(flt(doc.get("grand_total")))
    bgt = abs(flt(doc.get("base_grand_total")))
    if gt:
        return round(bgt / gt, 6)
    return flt(frappe.db.get_value("DGI Reference Value", f"Currency Rate::{ccy}", "value"))


def _currency_block(doc, dto):
    ccy = _foreign_ccy(doc)
    if not ccy:
        return
    dto["curCode"] = ccy
    dto["curRate"] = _cur_rate(doc)
    dt = get_datetime(f"{doc.posting_date} {doc.get('posting_time') or '00:00:00'}")
    dto["curDate"] = dt.strftime("%Y-%m-%d %H:%M:%S")


def _payments(doc, settings):
    ccy = _foreign_ccy(doc)
    rate = _cur_rate(doc) if ccy else None
    out = []
    for ln in resolve_payment_lines(doc, settings):
        row = {"name": ln["type"], "amount": ln["amount_cdf"]}
        if ccy:
            row["currencyCode"] = ccy
            row["currencyRate"] = rate
        out.append(row)
    return out


# --------------------------------------------------------------------------- #
# Credit notes
# --------------------------------------------------------------------------- #

def credit_reference(doc):
    """(reference, nature): the original Code DEF/DGI for a linked credit note, 'RRR' for a
    rebate not tied to a sale, else the reference typed by the user."""
    nature = (doc.get("custom_dgi_reference_type") or "").upper() or None
    if doc.get("return_against"):
        return frappe.db.get_value("Sales Invoice", doc.return_against, "custom_dgi_code_def"), nature
    if nature == "RRR":
        return "RRR", nature
    return (doc.get("custom_dgi_reference") or "").strip() or None, nature


# --------------------------------------------------------------------------- #
# Request
# --------------------------------------------------------------------------- #

def build_invoice_request(doc):
    settings = get_settings()
    mode = price_mode(doc, settings)
    dto = {
        "nif": _seller_nif(doc, settings),
        "rn": doc.name,
        "mode": mode,
        "isf": _seller_isf(doc, settings),
        "type": _invoice_type(doc, settings),
        "items": _items(doc, settings, mode),
        "operator": operator_for(doc),
    }
    client = _client(doc, settings)
    if client:
        dto["client"] = client
    payments = _payments(doc, settings)
    if payments:
        dto["payment"] = payments
    if dto["type"] in CREDIT_TYPES:
        ref, nature = credit_reference(doc)
        dto["reference"] = ref
        dto["referenceType"] = nature
        desc = (doc.get("custom_dgi_reference_desc") or REFERENCE_LABELS.get(nature or "", "") or "").strip()
        dto["referenceDesc"] = desc[:20] or None
    for letter in COMMENT_LETTERS:
        val = (doc.get("dgi_comment_" + letter) or "").strip()
        if val:
            dto["cmt" + letter] = val
    _currency_block(doc, dto)
    return dto


def validate_invoice_request(dto):
    errors = []
    if not dto.get("nif"):
        errors.append("nif manquant (Company.tax_id ou Settings.default_nif)")
    isf = (dto.get("isf") or "").strip()
    if not isf:
        errors.append("isf manquant: renseignez l'ISF (fourni par la DGI) dans DGI Compliance Settings.")
    elif isf.upper().replace("-", "") in ("AAABBBNN",):
        errors.append("isf invalide: 'AAA-BBB-NN' est un gabarit d'exemple - saisissez votre ISF reel.")
    elif len(isf) > 10:
        errors.append("isf trop long (10 caracteres maximum, format AAA-BBB-NN)")
    if dto.get("mode") not in ("ttc", "ht"):
        errors.append("mode doit etre 'ttc' ou 'ht'")
    if dto.get("type") not in INVOICE_TYPES:
        errors.append(f"type invalide ({dto.get('type')})")
    items = dto.get("items") or []
    if not items:
        errors.append("la facture doit contenir au moins un article")
    total = 0.0
    for i, it in enumerate(items, start=1):
        if not (it.get("name") or "").strip():
            errors.append(f"ligne {i}: nom d'article vide")
        if it.get("taxGroup") not in TAX_GROUPS:
            errors.append(f"ligne {i}: groupe de taxation invalide ({it.get('taxGroup')})")
        if not (flt(it.get("price")) > 0):
            errors.append(f"ligne {i}: prix nul ou negatif interdit (SFE 1.1.22)")
        if not (flt(it.get("quantity")) > 0):
            errors.append(f"ligne {i}: quantite nulle ou negative interdite (SFE 1.1.22)")
        if it.get("originalPrice") is not None and flt(it.get("originalPrice")) < flt(it.get("price")):
            errors.append(f"ligne {i}: prix d'origine inferieur au prix net")
        total += flt(it.get("price")) * flt(it.get("quantity"))
    if items and total <= 0:
        errors.append("montant de la facture nul ou negatif (SFE 1.1.21)")
    if not (dto.get("operator") or {}).get("name"):
        errors.append("nom de l'operateur manquant")
    client = dto.get("client") or {}
    if client.get("type") and client.get("type") not in CLIENT_TYPES:
        errors.append(f"type de client invalide ({client.get('type')})")
    for p in dto.get("payment") or []:
        if flt(p.get("amount")) < 0:
            errors.append("montant de paiement negatif")
    if dto.get("type") in CREDIT_TYPES:
        ref = (dto.get("reference") or "").strip()
        if not ref:
            errors.append("reference de la facture d'origine (Code DEF/DGI) obligatoire pour FA/EA")
        elif ref != "RRR" and len(ref.replace("-", "")) != 24:
            errors.append(f"reference '{ref}': un Code DEF/DGI compte 24 caracteres (hors tirets)")
        if dto.get("referenceType") not in REFERENCE_TYPES:
            errors.append("nature de l'avoir obligatoire (COR, RAN, RAM ou RRR)")
    return errors
