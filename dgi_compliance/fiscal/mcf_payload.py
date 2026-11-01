"""Message sent to the site gateway for one invoice (MCF channel).

Built from the SAME request as the e-MCF channel (edef.mapper.build_invoice_request), so both
channels share the fiscal rules of v5.0.5 (tax-group and payment mappings, discounts, credit
notes, comments A-H, exchange rate typed on the invoice). The gateway only encodes and executes.

Guards specific to the MCF protocol (refused BEFORE anything reaches the device):
  * tax groups: A-Z (FACNO v2.0). The DGI updated the groups without updating the protocol
    document; an updated MCF announces its groups in C1h (H6). When the groups of the MCF are known
    (last heartbeat), a group it does not know is refused here with a clear message;
  * CREDIT payment: declared on the MCF as on the e-MCF (H5, DGI). Protocol v1.0 does not document
    the 35h letter: the letter of the updated MCF is configured in DGI Compliance Settings;
  * currency other than USD: C0h only carries a USD rate (CRT/CDT).
"""
import json

import frappe
from frappe import _
from frappe.utils import flt, get_datetime

from dgi_compliance.edef.mapper import build_invoice_request, validate_invoice_request

PAYMENT_LETTERS = {"ESPECES": "E", "VIREMENT": "V", "CARTEBANCAIRE": "C", "MOBILEMONEY": "M",
                   "CHEQUES": "D", "AUTRE": "A"}
MCF_GROUPS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
COMMENT_LETTERS = "abcdefgh"
PAYLOAD_VERSION = 2


CREDIT_WITH_LETTER = "Declarer avec la lettre credit du MCF"
CREDIT_AS_OTHER = "Declarer en A (autre)"


class MCFPayloadError(frappe.ValidationError):
    """The invoice cannot be expressed with the MCF-SFE protocol."""


def credit_letter(settings, policy=None) -> str:
    policy = policy or settings.get("mcf_credit_policy") or CREDIT_WITH_LETTER
    if policy == CREDIT_AS_OTHER:
        return "A"
    if policy == CREDIT_WITH_LETTER:
        letter = (settings.get("mcf_credit_letter") or "").strip().upper()
        if len(letter) == 1 and "A" <= letter <= "Z":
            return letter
        frappe.throw(_("Vente a credit : renseignez la lettre du paiement a credit du MCF (DGI Compliance Settings > "
                       "Canal MCF > 'Lettre du paiement a credit'), communiquee par le fournisseur apres la mise a "
                       "jour du MCF."), exc=MCFPayloadError, title=_("Normalisation MCF"))
    frappe.throw(_("Vente a credit : declaration sur le MCF desactivee (parametre 'Paiement a credit sur MCF')."),
                 exc=MCFPayloadError, title=_("Normalisation MCF"))


def _device_groups(route) -> set:
    """Tax groups announced by the MCF in its last C1h (empty = unknown: the MCF decides)."""
    if not route or not route.device:
        return set()
    raw = frappe.db.get_value("DGI MCF Device", route.device, "tax_rates")
    try:
        return set((json.loads(raw) or {}).keys()) if raw else set()
    except ValueError:
        return set()


def _rate(letter):
    return flt(frappe.db.get_value("DGI Tax Group", letter, "rate") or 0)


def compute_totals(items, mode) -> dict:
    """Same arithmetic as the MCF: per group amount, VAT per group (TTC: extracted, HT: added),
    then MV (TTC total, specific tax included) and MT (VAT total)."""
    groups = {}
    ts = 0.0
    for row in items:
        g = groups.setdefault(row["tax_group"], {"rate": flt(row["tax_rate"]), "mon": 0.0})
        g["mon"] = round(g["mon"] + flt(row["amount"]), 2)
        ts = round(ts + flt(row.get("ts_total") or 0), 2)
    mv = mt = 0.0
    for g in groups.values():
        r = g["rate"]
        if mode == "TTC":
            vat = round(g["mon"] * r / (100.0 + r), 2) if r else 0.0
            mv = round(mv + g["mon"], 2)
        else:
            vat = round(g["mon"] * r / 100.0, 2) if r else 0.0
            mv = round(mv + g["mon"] + vat, 2)
        mt = round(mt + vat, 2)
    return {"mv": round(mv + ts, 2), "mt": mt, "ts": ts}


def build_mcf_invoice(doc, settings, route) -> tuple[dict, dict]:
    """(invoice message for the gateway, e-MCF request used for the fingerprint)."""
    dto = build_invoice_request(doc)
    if route and route.nif:
        dto["nif"] = route.nif
    errors = validate_invoice_request(dto)
    if errors:
        frappe.throw(_("Donnees de facture non conformes : ") + "; ".join(errors), exc=MCFPayloadError,
                     title=_("Normalisation MCF"))

    cur = dto.get("curCode")
    if cur and cur != "USD":
        frappe.throw(_("Le MCF ne transmet qu'un taux de change USD (commande C0h). Devise de la facture : {0}.")
                     .format(cur), exc=MCFPayloadError, title=_("Normalisation MCF"))

    mode = (dto.get("mode") or "ttc").upper()
    device_groups = _device_groups(route)
    items = []
    for i, src in enumerate(dto["items"], start=1):
        letter = (src.get("taxGroup") or "").upper()
        if len(letter) != 1 or letter not in MCF_GROUPS:
            frappe.throw(_("Ligne {0} : groupe de taxation {1} invalide.").format(i, letter or "?"),
                         exc=MCFPayloadError, title=_("Normalisation MCF"))
        if device_groups and letter not in device_groups:
            frappe.throw(_("Ligne {0} : le MCF {1} ne connait que les groupes {2}. Le groupe {3} demande la mise a "
                           "jour du MCF (groupes FACNO v2.0).").format(i, route.device, "".join(sorted(device_groups)),
                                                                       letter),
                         exc=MCFPayloadError, title=_("Normalisation MCF"))
        qty = flt(src.get("quantity") or 1)
        price = flt(src.get("price") or 0)
        row = {"name": (src.get("name") or "ARTICLE")[:64], "desc": (src.get("code") or None),
               "itype": src.get("type") or "BIE", "tax_group": letter, "tax_rate": _rate(letter),
               "amount": round(price * qty, 2), "price": price, "qty": qty}
        if src.get("originalPrice"):
            row["orig_price"] = flt(src["originalPrice"])
            row["price_desc"] = (src.get("priceModification") or "Remise")[:24]
        if src.get("taxSpecificValue") not in (None, ""):
            row["ts_value"] = str(src["taxSpecificValue"]).strip()
            row["ts_total"] = flt(src.get("taxSpecificAmount") or 0)
        items.append(row)

    policy = (settings.get("mcf_credit_policy") or CREDIT_WITH_LETTER)
    payments = []
    for p in dto.get("payment") or []:
        name = (p.get("name") or "").upper()
        if name == "CREDIT":
            letter = credit_letter(settings, policy)
        else:
            letter = PAYMENT_LETTERS.get(name)
            if not letter:
                frappe.throw(_("Mode de paiement {0} sans equivalent MCF.").format(name), exc=MCFPayloadError)
        payments.append({"letter": letter, "amount": flt(p.get("amount") or 0)})

    client = dto.get("client") or {}
    crccm = frappe.db.get_value("Customer", doc.customer, "dgi_registration_no") if doc.get("customer") else ""
    op = dto.get("operator") or {}
    inv = {
        "fn": doc.name[:64], "nif": dto.get("nif") or "", "isf": dto.get("isf") or "",
        "invoice_type": dto.get("type"), "pmode": mode,
        "operator": {"id": str(op.get("id") or "SFE").split("@")[0][:16], "name": str(op.get("name") or "SFE")[:32]},
        "client": {"ctype": client.get("type") or "PP", "cnif": client.get("nif") or "",
                   "cnom": client.get("name") or "", "cadd": client.get("address") or "",
                   "ctel": doc.get("contact_mobile") or "", "ceml": doc.get("contact_email") or "",
                   "crccm": crccm or ""},
        "items": items, "payments": payments,
        "comments": {c.upper(): dto["cmt" + c] for c in COMMENT_LETTERS if dto.get("cmt" + c)},
        "totals": compute_totals(items, mode),
        "erp_total_cdf": abs(flt(doc.get("base_grand_total") or 0)),
        "version": PAYLOAD_VERSION,
    }
    if dto.get("type") in ("FA", "EA"):
        inv["credit_note"] = {"rr": dto.get("referenceType") or "", "rn": (dto.get("reference") or "").replace("-", "")}
    if cur == "USD":
        cdt = get_datetime(dto.get("curDate") or doc.posting_date).strftime("%Y%m%d")
        inv["exchange"] = {"crt": flt(dto.get("curRate") or 0), "cdt": cdt}
    return inv, dto
