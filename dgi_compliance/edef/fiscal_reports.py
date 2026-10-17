"""Statistical reports X, Z and A (SFE specifications fev. 2026, sections 1.3 and 1.4).

Source data = invoices registered at the DGI (a Code DEF/DGI is present), dated by their
normalization time (custom_dgi_normalized_on). Amounts are the CDF figures returned by the
e-MCF (stored with each invoice since v5.0.0); for invoices normalized by an older version
they are recomputed with the SFE calculation rules.

  * Z report : period since the previous Z report (numbered, opens a new period);
  * X daily  : period since the last Z report (no new period);
  * X periodic: period chosen by the user;
  * A report : per article, since the previous A report.
"""
import json
from collections import OrderedDict

import frappe
from frappe import _
from frappe.utils import add_to_date, flt, get_datetime, now_datetime

from dgi_compliance.edef.calc import compute_totals, response_groups, round2

INVOICE_TYPES = ("FV", "FT", "FA", "EV", "ET", "EA")
CREDIT_TYPES = ("FA", "EA")
GROUPS = list("ABCDEFGHIJKLMNOPQRS")
PAYMENT_TYPES = ["ESPECES", "MOBILEMONEY", "CHEQUES", "CARTEBANCAIRE", "VIREMENT", "CREDIT", "AUTRE"]
PAYMENT_LABELS = {"ESPECES": "ESPECES", "MOBILEMONEY": "MOBILE MONEY", "CHEQUES": "CHEQUES",
                  "CARTEBANCAIRE": "CARTE BANCAIRE", "VIREMENT": "VIREMENT", "CREDIT": "CREDIT",
                  "AUTRE": "AUTRE"}
NATURES = OrderedDict([("COR", "Correction"), ("RAN", "Annulation"), ("RAM", "Avoir suite reprise"),
                       ("RRR", "Remise, ristourne, rabais")])
EPOCH = "2000-01-01 00:00:00"


# --------------------------------------------------------------------------- #
# Fiscal data of one invoice
# --------------------------------------------------------------------------- #

def _invoice_fiscal(row):
    """{type, nature, groups{G: total/ht/vat/ts}, total, vtotal, ts, payments{T: amount},
    reductions, reductions_tax, items[...]} in CDF for one registered invoice."""
    data = {}
    try:
        data = json.loads(row.custom_dgi_response or "{}") or {}
    except (TypeError, ValueError):
        data = {}
    req = data.get("request") or {}
    resp = data.get("invoice") or {}
    if not req or resp.get("total") is None:
        doc = frappe.get_doc("Sales Invoice", row.name)
        from dgi_compliance.edef.mapper import build_invoice_request
        try:
            frappe.flags.dgi_report_rebuild = True
            req = build_invoice_request(doc)
        finally:
            frappe.flags.dgi_report_rebuild = False
        from dgi_compliance.edef.mapper import group_rate
        rates = {it["taxGroup"]: group_rate(it["taxGroup"]) for it in req.get("items") or []}
        calc = compute_totals(req.get("items") or [], req.get("mode"), rates)
        groups = {g: dict(v) for g, v in calc["groups"].items()}
        total, vtotal, ts = calc["total"], calc["vtotal"], calc["ts"]
    else:
        groups = response_groups(resp)
        total, vtotal, ts = round2(resp.get("total")), round2(resp.get("vtotal")), round2(resp.get("ts"))
    mode = (req.get("mode") or "ttc").lower()

    ts_by_group, red, red_tax = {}, 0.0, 0.0
    for it in req.get("items") or []:
        g = it.get("taxGroup")
        ts_by_group[g] = ts_by_group.get(g, 0.0) + flt(it.get("taxSpecificAmount"))
        if it.get("originalPrice") is not None:
            r_amount = round2((flt(it.get("originalPrice")) - flt(it.get("price"))) * flt(it.get("quantity")))
            rate = flt((groups.get(g) or {}).get("rate"))
            red += r_amount
            if rate:
                red_tax += r_amount * rate / (100.0 + rate) if mode == "ttc" else r_amount * rate / 100.0
    for g, v in groups.items():
        v["ts"] = round2(ts_by_group.get(g, 0.0))

    payments = {}
    for p in req.get("payment") or [{"name": "ESPECES", "amount": total}]:
        payments[p.get("name") or "AUTRE"] = payments.get(p.get("name") or "AUTRE", 0.0) + flt(p.get("amount"))
    return {
        "type": row.dgi_invoice_type or req.get("type") or "FV",
        "nature": (row.custom_dgi_reference_type or req.get("referenceType") or "").upper(),
        "groups": groups, "total": total, "vtotal": vtotal, "ts": ts,
        "ht": round2(total - vtotal - ts),
        "payments": payments, "reductions": round2(red), "reductions_tax": round2(red_tax),
        "items": req.get("items") or [],
    }


def _registered_invoices(company, start, end):
    return frappe.get_all(
        "Sales Invoice",
        filters=[["company", "=", company], ["custom_dgi_code_def", "is", "set"],
                 ["custom_dgi_normalized_on", ">", start], ["custom_dgi_normalized_on", "<=", end]],
        fields=["name", "dgi_invoice_type", "custom_dgi_reference_type", "custom_dgi_response",
                "custom_dgi_normalized_on"],
        order_by="custom_dgi_normalized_on asc")


def _incomplete_by_type(company, start, end):
    """Sales whose normalization was started (request sent to the e-MCF) but never completed."""
    names = frappe.get_all("DGI Exchange Log",
                           filters=[["direction", "=", "create"], ["creation", ">", start],
                                    ["creation", "<=", end], ["reference_invoice", "is", "set"]],
                           pluck="reference_invoice", distinct=True)
    out = {t: 0 for t in INVOICE_TYPES}
    for name in set(names):
        inv = frappe.db.get_value("Sales Invoice", name,
                                  ["company", "custom_dgi_code_def", "dgi_invoice_type"], as_dict=True)
        if inv and inv.company == company and not inv.custom_dgi_code_def:
            out[inv.dgi_invoice_type or "FV"] = out.get(inv.dgi_invoice_type or "FV", 0) + 1
    return out


# --------------------------------------------------------------------------- #
# X / Z
# --------------------------------------------------------------------------- #

def compute_xz(company, start, end):
    by_type = OrderedDict((t, {"count": 0, "incomplete": 0, "total": 0.0, "ht": 0.0, "vat": 0.0, "ts": 0.0})
                          for t in INVOICE_TYPES)
    by_type_group = OrderedDict((t, OrderedDict((g, {"total": 0.0, "ht": 0.0, "vat": 0.0, "ts": 0.0})
                                                 for g in GROUPS)) for t in INVOICE_TYPES)
    pay = OrderedDict((p, {"sales": 0.0, "credits": 0.0}) for p in PAYMENT_TYPES)
    reductions = OrderedDict((t, {"amount": 0.0, "tax": 0.0}) for t in INVOICE_TYPES)
    natures = OrderedDict((n, {"count": 0, "total": 0.0}) for n in NATURES)

    for row in _registered_invoices(company, start, end):
        f = _invoice_fiscal(row)
        t = f["type"] if f["type"] in by_type else "FV"
        s = by_type[t]
        s["count"] += 1
        for k in ("total", "ht", "vat", "ts"):
            s[k] = round2(s[k] + f["vtotal" if k == "vat" else k])
        for g, v in f["groups"].items():
            cell = by_type_group[t].setdefault(g, {"total": 0.0, "ht": 0.0, "vat": 0.0, "ts": 0.0})
            for k in ("total", "ht", "vat", "ts"):
                cell[k] = round2(cell[k] + flt(v.get(k)))
        for ptype, amount in f["payments"].items():
            cell = pay.setdefault(ptype, {"sales": 0.0, "credits": 0.0})
            cell["credits" if t in CREDIT_TYPES else "sales"] = round2(
                cell["credits" if t in CREDIT_TYPES else "sales"] + amount)
        reductions[t]["amount"] = round2(reductions[t]["amount"] + f["reductions"])
        reductions[t]["tax"] = round2(reductions[t]["tax"] + f["reductions_tax"])
        if t in CREDIT_TYPES and f["nature"] in natures:
            natures[f["nature"]]["count"] += 1
            natures[f["nature"]]["total"] = round2(natures[f["nature"]]["total"] + f["total"])

    incomplete = _incomplete_by_type(company, start, end)
    for t, n in incomplete.items():
        if t in by_type:
            by_type[t]["incomplete"] = n

    sales = [by_type[t] for t in INVOICE_TYPES if t not in CREDIT_TYPES]
    credits = [by_type[t] for t in CREDIT_TYPES]
    return {
        "by_type": by_type,
        "by_type_group": by_type_group,
        "payments": [{"type": p, "label": PAYMENT_LABELS.get(p, p), "sales": v["sales"],
                      "credits": v["credits"], "net": round2(v["sales"] - v["credits"])} for p, v in pay.items()],
        "reductions": reductions,
        "natures": [{"code": n, "label": NATURES[n], **v} for n, v in natures.items()],
        "incomplete_total": sum(v["incomplete"] for v in by_type.values()),
        "summary": {
            "count": sum(v["count"] for v in by_type.values()),
            "sales_total": round2(sum(v["total"] for v in sales)),
            "credits_total": round2(sum(v["total"] for v in credits)),
            "net_total": round2(sum(v["total"] for v in sales) - sum(v["total"] for v in credits)),
            "net_vat": round2(sum(v["vat"] for v in sales) - sum(v["vat"] for v in credits)),
        },
    }


# --------------------------------------------------------------------------- #
# A report
# --------------------------------------------------------------------------- #

def compute_a(company, start, end):
    articles = OrderedDict()
    for row in _registered_invoices(company, start, end):
        f = _invoice_fiscal(row)
        credit = f["type"] in CREDIT_TYPES
        si_items = frappe.get_all("Sales Invoice Item", filters={"parent": row.name},
                                  fields=["item_code", "item_name", "idx"], order_by="idx asc")
        for si, it in zip(si_items, f["items"]):
            code = si.item_code or it.get("code") or it.get("name")
            a = articles.setdefault(code, {
                "code": code, "name": si.item_name or it.get("name"), "price": 0.0,
                "group": it.get("taxGroup"), "rate": flt((f["groups"].get(it.get("taxGroup")) or {}).get("rate")),
                "qty_sold": 0.0, "qty_returned": 0.0, "amount": 0.0, "stock": None})
            qty = flt(it.get("quantity"))
            amount = round2(flt(it.get("price")) * qty)
            if credit:
                a["qty_returned"] = round(a["qty_returned"] + qty, 3)
                a["amount"] = round2(a["amount"] - amount)
            else:
                a["qty_sold"] = round(a["qty_sold"] + qty, 3)
                a["amount"] = round2(a["amount"] + amount)
                a["price"] = flt(it.get("price"))
    for code, a in articles.items():
        if frappe.db.exists("Item", code) and frappe.db.get_value("Item", code, "is_stock_item"):
            warehouses = frappe.get_all("Warehouse", filters={"company": company}, pluck="name")
            a["stock"] = round(flt(frappe.db.sql(
                "select sum(actual_qty) from `tabBin` where item_code=%s and warehouse in %s",
                (code, tuple(warehouses) or ("",)))[0][0]), 3) if warehouses else 0.0
    rows = sorted(articles.values(), key=lambda r: str(r["code"]))
    return {"articles": rows,
            "summary": {"count": len(rows),
                        "amount": round2(sum(r["amount"] for r in rows)),
                        "qty_sold": round(sum(r["qty_sold"] for r in rows), 3),
                        "qty_returned": round(sum(r["qty_returned"] for r in rows), 3)}}


# --------------------------------------------------------------------------- #
# Periods
# --------------------------------------------------------------------------- #

def last_report_end(company, kinds):
    last = frappe.get_all("DGI Rapport Fiscal", filters={"company": company, "report_type": ["in", kinds]},
                          fields=["to_datetime"], order_by="to_datetime desc", limit=1)
    return get_datetime(last[0].to_datetime) if last else None


def first_registration(company):
    first = frappe.get_all("Sales Invoice", filters={"company": company, "custom_dgi_code_def": ["is", "set"]},
                           fields=["custom_dgi_normalized_on"], order_by="custom_dgi_normalized_on asc", limit=1)
    if first and first[0].custom_dgi_normalized_on:
        return add_to_date(get_datetime(first[0].custom_dgi_normalized_on), seconds=-1)
    return get_datetime(EPOCH)


def period_for(report_type, company, from_datetime=None, to_datetime=None):
    end = get_datetime(to_datetime) if (report_type == "X périodique" and to_datetime) else now_datetime()
    if report_type == "X périodique":
        if not from_datetime:
            frappe.throw(_("Rapport X périodique: la date de début est obligatoire."))
        start = get_datetime(from_datetime)
    elif report_type == "A":
        start = last_report_end(company, ["A"]) or first_registration(company)
    else:  # Z and daily X: since the last Z report
        start = last_report_end(company, ["Z"]) or first_registration(company)
    if start > end:
        frappe.throw(_("Periode invalide: le debut ({0}) est posterieur a la fin ({1}).").format(start, end))
    return start, end
