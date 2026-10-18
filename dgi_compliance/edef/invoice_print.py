"""Data for the normalized invoice print format "DGI Facture Normalisee" (v5.0.0).

Everything printed is fetched from ERPNext / the stored e-MCF exchange - nothing is typed at
print time. Commercial amounts stay in the invoice currency; the fiscal amounts registered by
the e-MCF are always in CDF (shown under "Net a payer" for a foreign-currency invoice); the
amount in words is always expressed in Congolese francs.

Mandatory mentions: SFE specifications fev. 2026 section 1.2.1 a-x, Protocole MCF-SFE section 2.
"""
import json

import frappe
from frappe.utils import flt, formatdate, format_time, format_datetime, strip_html

from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings
from dgi_compliance.edef.calc import compute_totals, response_groups, round2
from dgi_compliance.edef.words import amount_in_words_cdf

TITLES = {
    "FV": ("FACTURE DE VENTE", None),
    "FT": ("FACTURE D'ACOMPTE", None),
    "FA": ("FACTURE D'AVOIR", None),
    "EV": ("FACTURE DE VENTE", "EXPORTATION"),
    "ET": ("FACTURE D'ACOMPTE", "EXPORTATION"),
    "EA": ("FACTURE D'AVOIR", "EXPORTATION"),
}
NATURES = {"COR": "Correction", "RAN": "Annulation", "RAM": "Avoir suite reprise de biens/services",
           "RRR": "Remise, ristourne, rabais"}
CLIENT_TYPES = {"PP": "Personne physique", "PM": "Personne morale",
                "PC": "Personne physique commerçante", "PL": "Profession libérale",
                "AO": "Ambassades et organisations internationales"}


def _lines_text(html):
    txt = strip_html((html or "").replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n"))
    return [ln.strip() for ln in txt.splitlines() if ln.strip()]


def _response(doc):
    try:
        data = json.loads(doc.get("custom_dgi_response") or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def _company_ccy(doc):
    return frappe.get_cached_value("Company", doc.company, "default_currency") or "CDF"


def _is_foreign(doc):
    ccy = doc.get("currency")
    return bool(ccy and ccy not in (_company_ccy(doc), "CDF"))


# --------------------------------------------------------------------------- #

def company_block(doc, settings):
    c = frappe.get_cached_doc("Company", doc.company)
    address_html = doc.get("company_address_display")
    if not address_html:
        try:
            from frappe.contacts.doctype.address.address import get_company_address
            address_html = (get_company_address(doc.company) or {}).get("company_address_display")
        except Exception:
            address_html = ""
    return {
        "name": c.company_name or doc.company,
        "logo": c.get("company_logo"),
        "address": _lines_text(address_html),
        "email": c.get("email") or "",
        "phone": c.get("phone_no") or "",
        "nif": (settings.default_nif or c.get("tax_id") or "").strip(),
        "rccm": c.get("dgi_rccm") or "",
        "id_nat": c.get("dgi_id_nat") or "",
        "isf": (settings.isf or "").strip(),
        "website": c.get("website") or "",
    }


def _point_of_sale_name(doc, settings):
    nim = doc.get("custom_dgi_nim")
    if nim:
        pos = frappe.db.get_value("DGI Point of Sale", {"nim": nim}, "name")
        if pos:
            return pos, nim
    if doc.get("pos_profile"):
        pos = frappe.db.get_value("POS Profile", doc.pos_profile, "dgi_point_of_sale")
        if pos:
            return pos, nim
    if settings.get("default_point_of_sale"):
        return settings.default_point_of_sale, nim
    active = frappe.get_all("DGI Point of Sale", filters={"is_active": 1}, pluck="name", limit=2)
    return (active[0] if len(active) == 1 else None), nim


def pos_block(doc, settings):
    """Point de vente (modele DGI): code magasin, adresse, ville, reference NID."""
    pos_name, nim = _point_of_sale_name(doc, settings)
    pos = frappe.get_cached_doc("DGI Point of Sale", pos_name) if pos_name else None
    emcf_nim = nim or (pos.nim if pos else None)
    emcf = frappe.get_cached_doc("DGI EMCF", emcf_nim) if emcf_nim and frappe.db.exists("DGI EMCF", emcf_nim) else None
    address = (pos.get("pos_address") if pos else None) or ", ".join(
        x for x in ((emcf.address1, emcf.address2) if emcf else ()) if x)
    return {
        "name": (pos.pos_name if pos else None) or (emcf.shop_name if emcf else ""),
        "code": (pos.get("pos_code") if pos else None) or emcf_nim or "",
        "address": address or "",
        "city": (pos.get("pos_city") if pos else None) or (emcf.address3 if emcf else "") or "",
        "phone": (pos.get("pos_phone") if pos else None) or (emcf.contact1 if emcf else "") or "",
        "nid": (pos.nid if pos and pos.nid else None) or emcf_nim or "",
        "terminal": (pos.get("pos_terminal") if pos else None) or doc.get("pos_profile") or "",
        "found": bool(pos or emcf),
    }


def client_block(doc, settings):
    from dgi_compliance.edef.mapper import _client_dgi_type
    ctype = _client_dgi_type(doc, settings) or ""
    country, city = "", ""
    if doc.get("customer_address"):
        a = frappe.db.get_value("Address", doc.customer_address, ["city", "country"], as_dict=True) or {}
        city, country = a.get("city") or "", a.get("country") or ""
    return {
        "code": doc.get("customer") or "",
        "name": doc.get("customer_name") or doc.get("customer") or "",
        "address": _lines_text(doc.get("address_display")),
        "city": city,
        "country": country,
        "type_code": ctype,
        "type_label": f"[{ctype}] {CLIENT_TYPES.get(ctype, '')}".strip() if ctype else "",
        "contact": strip_html(doc.get("contact_display") or ""),
        "phone": doc.get("contact_mobile") or "",
        "email": doc.get("contact_email") or "",
        "nif": doc.get("tax_id") or "",
        "registration": (frappe.db.get_value("Customer", doc.customer, "dgi_registration_no")
                         if doc.get("customer") else "") or "",
    }


def _payment_terms(doc):
    if doc.get("payment_terms_template"):
        return doc.payment_terms_template
    sched = doc.get("payment_schedule") or []
    terms = [s.get("payment_term") for s in sched if s.get("payment_term")]
    if terms:
        return ", ".join(dict.fromkeys(terms))
    if doc.get("due_date") and doc.get("posting_date") and str(doc.due_date) != str(doc.posting_date):
        return f"Échéance {formatdate(doc.due_date)}"
    return "Comptant"


def _sales_orders(doc):
    so = [it.get("sales_order") for it in (doc.get("items") or []) if it.get("sales_order")]
    return ", ".join(dict.fromkeys(so)) or doc.get("po_no") or ""


def _group_label(group):
    return "A-EX" if group == "A" else group


def print_lines(doc, settings):
    """Commercial lines in the invoice currency + fiscal attributes from the mapper."""
    from dgi_compliance.edef.mapper import build_lines, price_mode
    mode = price_mode(doc, settings)
    try:
        fiscal = {ln["idx"]: ln for ln in build_lines(doc, settings, mode)}
    except Exception:
        fiscal = {}
    rate_ccy = flt(doc.get("conversion_rate")) or 1.0
    out = []
    for it in (doc.get("items") or []):
        f = fiscal.get(it.get("idx")) or {}
        qty = abs(flt(it.get("qty")))
        rate = abs(flt(it.get("rate")))
        plr = abs(flt(it.get("price_list_rate")))
        unit = plr if plr > rate else rate
        gross = round2(unit * qty)
        amount = abs(flt(it.get("amount")))
        out.append({
            "idx": it.get("idx"),
            "code": f.get("code") or it.get("item_code") or "",
            "name": it.get("item_name") or it.get("description") or "",
            "type": f.get("type") or "",
            "qty": qty,
            "uom": it.get("uom") or "",
            "unit_price": unit,
            "gross": gross,
            "discount": round2(max(gross - amount, 0)),
            "discount_label": f.get("discount_label") or "",
            "amount": amount,
            "group": f.get("group") or "",
            "group_label": _group_label(f.get("group") or ""),
            "rate": f.get("rate"),
            "ts_value": f.get("ts_value") or "",
            "ts_label": ((f.get("ts_value") or "") if str(f.get("ts_value") or "").endswith("%")
                         else f"{f.get('ts_value')} CDF / unité") if f.get("ts_value") else "",
            "ts_amount": round2(flt(f.get("ts_amount")) / rate_ccy) if f.get("ts_amount") else 0.0,
        })
    return out, mode


def _computed_fiscal(doc, settings):
    """Fiscal breakdown in CDF rebuilt from the request (no stored e-MCF response)."""
    from dgi_compliance.edef.mapper import build_lines, group_rate
    lines = build_lines(doc, settings)
    items = [{"price": ln["price"], "quantity": ln["quantity"], "taxGroup": ln["group"],
              "taxSpecificAmount": ln["ts_amount"]} for ln in lines]
    rates = {ln["group"]: group_rate(ln["group"]) for ln in lines}
    from dgi_compliance.edef.mapper import price_mode
    return compute_totals(items, price_mode(doc, settings), rates)


def fiscal_cdf(doc, settings):
    """Fiscal figures in CDF: from the stored e-MCF response when available."""
    resp = (_response(doc).get("invoice") or {})
    if resp.get("total") is not None:
        groups = response_groups(resp)
        return {
            "source": "e-MCF",
            "total": round2(resp.get("total")),
            "vtotal": round2(resp.get("vtotal")),
            "ts": round2(resp.get("ts")),
            "groups": [{"group": g, "label": _group_label(g), **v} for g, v in sorted(groups.items())],
        }
    try:
        calc = _computed_fiscal(doc, settings)
    except Exception:
        calc = {"total": abs(flt(doc.get("base_grand_total"))),
                "vtotal": abs(flt(doc.get("base_total_taxes_and_charges"))), "ts": 0.0, "groups": {}}
    return {
        "source": "calcul",
        "total": calc["total"], "vtotal": calc["vtotal"], "ts": calc.get("ts", 0.0),
        "groups": [{"group": g, "label": _group_label(g), **v} for g, v in sorted((calc.get("groups") or {}).items())],
    }


def _commercial_tax_table(doc, lines):
    """Per-group VAT in the invoice currency, allocated from the ERPNext tax total so that the
    table always adds up to the printed 'Total TVA'."""
    groups = {}
    for ln in lines:
        groups.setdefault(ln["group"], {"group": ln["group"], "label": ln["group_label"],
                                        "rate": flt(ln["rate"]), "ht": 0.0, "vat": 0.0})
    net_by_group = {}
    for it, ln in zip(doc.get("items") or [], lines):
        net_by_group[ln["group"]] = net_by_group.get(ln["group"], 0.0) + abs(flt(it.get("net_amount")))
    total_vat = abs(flt(doc.get("total_taxes_and_charges")))
    weights = {g: net_by_group.get(g, 0.0) * flt(v["rate"]) for g, v in groups.items()}
    wsum = sum(weights.values())
    allocated = 0.0
    keys = sorted(groups)
    for i, g in enumerate(keys):
        groups[g]["ht"] = round2(net_by_group.get(g, 0.0))
        if wsum:
            vat = round2(total_vat - allocated) if i == len(keys) - 1 else round2(total_vat * weights[g] / wsum)
        else:
            vat = 0.0
        allocated += vat
        groups[g]["vat"] = vat
        groups[g]["total"] = round2(groups[g]["ht"] + vat)
    return [groups[g] for g in keys]


def totals_block(doc, settings, lines, fiscal):
    foreign = _is_foreign(doc)
    discount_lines = round2(sum(ln["discount"] for ln in lines))
    discount_doc = abs(flt(doc.get("discount_amount")))
    if foreign:
        ttc = abs(flt(doc.get("grand_total")))
        vat = abs(flt(doc.get("total_taxes_and_charges")))
        ts = round2(sum(ln["ts_amount"] for ln in lines))
        table = _commercial_tax_table(doc, lines)
    else:
        ttc, vat, ts, table = fiscal["total"], fiscal["vtotal"], fiscal["ts"], fiscal["groups"]
    rounded = abs(flt(doc.get("rounded_total")))
    show_rounded = bool(rounded) and not doc.get("disable_rounded_total") and abs(rounded - ttc) >= 0.01
    return {
        "currency": doc.get("currency"),
        "gross": round2(ttc - vat - ts + discount_lines + discount_doc),
        "discount": round2(discount_lines + discount_doc),
        "ht": round2(ttc - vat - ts),
        "ts": ts,
        "vat": vat,
        "ttc": ttc,
        "rounded": rounded if show_rounded else None,
        "table": table,
    }


def security_block(doc):
    return {
        "code_def": doc.get("custom_dgi_code_def") or "",
        "nim": doc.get("custom_dgi_nim") or "",
        "counters": doc.get("custom_dgi_counters") or "",
        "datetime": doc.get("custom_dgi_datetime") or "",
        "qr_image": doc.get("custom_dgi_qr_image") or "",
        "qr_code": doc.get("custom_dgi_qr_code") or "",
    }


def _rate_text(rate):
    """Exchange rate exactly as transmitted (up to 6 decimals, at least 2): 2 850.00 / 2 850.5525."""
    txt = f"{flt(rate):,.6f}".rstrip("0")
    whole, _, dec = txt.partition(".")
    return f"{whole.replace(',', ' ')}.{dec.ljust(2, '0')}"


@frappe.whitelist()
def dgi_invoice_context(doc):
    """Single Jinja entry point used by the print format."""
    if isinstance(doc, str):
        doc = frappe.get_doc("Sales Invoice", doc)
    settings = get_settings()
    from dgi_compliance.edef.mapper import _invoice_type, credit_reference
    from dgi_compliance.edef.payments import resolve_payment_lines

    itype = doc.get("dgi_invoice_type") or _invoice_type(doc, settings)
    title, export = TITLES.get(itype, ("FACTURE", None))
    lines, mode = print_lines(doc, settings)
    comments_flag_d = any(ln["group"] == "D" for ln in lines)
    fiscal = fiscal_cdf(doc, settings)
    totals = totals_block(doc, settings, lines, fiscal)
    client = client_block(doc, settings)
    try:
        payments = resolve_payment_lines(doc, settings)
    except Exception as e:
        payments = [{"label": str(e)[:80], "type_label": "", "amount": 0.0, "amount_cdf": 0.0, "source": "error"}]

    foreign = _is_foreign(doc)
    from dgi_compliance.edef.mapper import _cur_rate
    cur_rate = flt(_cur_rate(doc)) if foreign else 1.0
    cdf_ttc = fiscal["total"] if fiscal["source"] == "e-MCF" else abs(flt(doc.get("base_grand_total")))
    cdf_vat = fiscal["vtotal"] if fiscal["source"] == "e-MCF" else abs(flt(doc.get("base_total_taxes_and_charges")))

    credit = None
    if itype in ("FA", "EA"):
        ref, nature = credit_reference(doc)
        credit = {"nature": nature or "", "nature_label": NATURES.get(nature or "", ""),
                  "reference": ref or "", "original": doc.get("return_against") or ""}

    comments = []
    for letter in "abcdefgh":
        val = (doc.get("dgi_comment_" + letter) or "").strip()
        if val:
            label = "Réf. Exo." if (letter == "a" and (client["type_code"] == "AO" or comments_flag_d)) \
                else f"Commentaire {letter.upper()}"
            comments.append({"label": label, "value": val})

    count = int(doc.get("custom_dgi_print_count") or 0)
    operator_id = doc.get("custom_dgi_operator") or doc.get("owner") or ""
    operator_name = doc.get("custom_dgi_operator_name") or frappe.utils.get_fullname(operator_id) or operator_id
    posting_time = format_time(doc.get("posting_time")) if doc.get("posting_time") else ""

    from dgi_compliance import __version__
    return {
        "app_version": __version__,
        "normalized": doc.get("custom_dgi_status") == "Normalized" and bool(doc.get("custom_dgi_code_def")),
        "title": title,
        "export": export,
        "invoice_type": itype,
        "invoice_type_label": (frappe.db.get_value("DGI Invoice Type", itype, "description") or title.title()),
        "copy_label": "DUPLICATA" if count >= 2 else "ORIGINAL",
        "company": company_block(doc, settings),
        "pos": pos_block(doc, settings),
        "client": client,
        "general": {
            "number": doc.name,
            "orders": _sales_orders(doc),
            "date": formatdate(doc.get("posting_date")),
            "time": posting_time,
            "due_date": formatdate(doc.get("due_date")) if doc.get("due_date") else "",
            "payment_terms": _payment_terms(doc),
            "payment_modes": ", ".join(p["label"] if p.get("type_label") in (p.get("label"), "") else
                                       f"{p['label']} ({p['type_label']})" for p in payments),
            "currency": doc.get("currency"),
            "created": format_datetime(doc.get("creation")) if doc.get("creation") else "",
            "operator_name": operator_name,
            "operator_id": operator_id,
            "price_mode": "TTC" if mode == "ttc" else "HT",
        },
        "lines": lines,
        "item_count": len(lines),
        "totals": totals,
        "payments": payments,
        "foreign": foreign,
        "cur_rate": cur_rate,
        "cur_rate_txt": _rate_text(cur_rate),
        "cdf": {"ttc": cdf_ttc, "vat": cdf_vat, "ht": round2(cdf_ttc - cdf_vat - flt(fiscal.get("ts"))),
                "source": fiscal["source"]},
        "amount_in_words": amount_in_words_cdf(cdf_ttc),
        "credit": credit,
        "comments": comments,
        "security": security_block(doc),
    }
