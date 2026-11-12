"""DGI Rapport Fiscal: X (quotidien / periodique), Z and A statistical reports, frozen at creation.

A report can neither be modified nor deleted: it is part of the electronic journal (SFE 1.1.20).
"""
import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_datetime, now_datetime

from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings
from dgi_compliance.edef import fiscal_reports as fr

CODES = {"X quotidien": "X", "X périodique": "X", "Z": "Z", "A": "A"}


class DGIRapportFiscal(Document):
    def autoname(self):
        code = CODES.get(self.report_type, "X")
        abbr = frappe.get_cached_value("Company", self.company, "abbr") or "CO"
        kinds = [k for k, v in CODES.items() if v == code]
        last = frappe.db.sql(
            "select max(sequence_no) from `tabDGI Rapport Fiscal` where company=%s and report_type in %s for update",
            (self.company, tuple(kinds)))[0][0] or 0
        self.sequence_no = int(last) + 1
        self.name = f"{code}-{abbr}-{self.sequence_no:05d}"

    def validate(self):
        if not self.is_new():
            frappe.throw(_("Un rapport fiscal est fige a sa generation et ne peut pas etre modifie."))
        if self.report_type not in CODES:
            frappe.throw(_("Type de rapport invalide."))

    def before_insert(self):
        settings = get_settings()
        start, end = fr.period_for(self.report_type, self.company, self.from_datetime, self.to_datetime)
        self.from_datetime, self.to_datetime = start, end
        company = frappe.get_cached_doc("Company", self.company)
        self.company_name = company.company_name
        self.nif = (settings.default_nif or company.tax_id or "").strip()
        self.isf = (settings.isf or "").strip()
        self.generated_on = now_datetime()
        self.generated_by = frappe.session.user
        if self.report_type == "A":
            data = fr.compute_a(self.company, start, end)
            self.invoice_count = data["summary"]["count"]
            self.total_ttc = data["summary"]["amount"]
            self.total_vat = 0
            self.incomplete_count = 0
        else:
            data = fr.compute_xz(self.company, start, end)
            self.invoice_count = data["summary"]["count"]
            self.total_ttc = data["summary"]["net_total"]
            self.total_vat = data["summary"]["net_vat"]
            self.incomplete_count = data["incomplete_total"]
        self.report_data = json.dumps(data, ensure_ascii=False, default=str)
        self.report_title = f"Rapport {self.report_type} - {start:%d/%m/%Y %H:%M} au {end:%d/%m/%Y %H:%M}"

    def after_insert(self):
        from dgi_compliance.edef.audit import log_exchange
        log_exchange(f"rapport-{CODES.get(self.report_type, 'X')}",
                     {"rapport": self.name, "type": self.report_type, "du": str(self.from_datetime),
                      "au": str(self.to_datetime)},
                     {"factures": self.invoice_count, "total_ttc": self.total_ttc, "tva": self.total_vat})

    def on_trash(self):
        frappe.throw(_("Les rapports fiscaux font partie du journal electronique: suppression interdite."))

    def get_data(self):
        try:
            return json.loads(self.report_data or "{}")
        except ValueError:
            return {}


@frappe.whitelist()
def generate(report_type, company=None, from_datetime=None, to_datetime=None):
    """Create and return a new report (buttons 'Rapport X / Z / A')."""
    if report_type not in CODES:
        frappe.throw(_("Type de rapport invalide."))
    company = company or frappe.defaults.get_user_default("Company") or frappe.get_all("Company", pluck="name", limit=1)[0]
    doc = frappe.get_doc({"doctype": "DGI Rapport Fiscal", "report_type": report_type, "company": company,
                          "from_datetime": from_datetime, "to_datetime": to_datetime})
    doc.insert()
    return doc.name


def _num(v):
    """Amount as printed on the DGI e-UF reports: 1.234.567,89 (no currency symbol)."""
    return f"{flt(v):,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")


def _qty(v):
    txt = f"{flt(v):,.3f}".rstrip("0").rstrip(".")
    return txt.replace(",", " ").replace(".", ",").replace(" ", ".")


def hide_zero_rows():
    """Setting 'Masquer les lignes a zero dans les rapports X et Z' (v5.4.2; switched on at installation and
    by the patch v5_4.rapports_par_pdv)."""
    return bool(frappe.utils.cint(get_settings().get("report_hide_zero_rows")))


def _nonzero(*values):
    return any(abs(flt(v)) >= 0.005 for v in values)


def _a_block(data):
    articles = data.get("articles") or []
    summ = data.get("summary") or {}
    return {"articles": [{
        "code": a.get("code") or "", "name": a.get("name") or "", "price": _num(a.get("price")),
        "group": a.get("group") or "", "rate": f"{flt(a.get('rate')):g} %",
        "qty_sold": _qty(a.get("qty_sold")), "qty_returned": _qty(a.get("qty_returned")),
        "stock": _qty(a.get("stock")) if a.get("stock") is not None else "n/a",
        "amount": _num(a.get("amount"))} for a in articles],
        "total": {"count": summ.get("count", len(articles)), "qty_sold": _qty(summ.get("qty_sold")),
                  "qty_returned": _qty(summ.get("qty_returned")), "amount": _num(summ.get("amount"))}}


def _xz_block(data, hide_zero=False):
    """Formatted rows of one scope. hide_zero: the rows without any amount or count are left out."""
    by_type = data.get("by_type") or {}
    by_type_group = data.get("by_type_group") or {}
    empty = {"count": 0, "incomplete": 0, "total": 0, "ht": 0, "vat": 0, "ts": 0}
    types = []
    for t in fr.INVOICE_TYPES:
        s = by_type.get(t) or empty
        groups_data = by_type_group.get(t) or {}
        # Groups A..S always listed (as on the DGI report); reserved groups only when used.
        letters = list(fr.GROUPS) + sorted(g for g, v in groups_data.items()
                                           if g not in fr.GROUPS and any(flt(x) for x in v.values()))
        groups = []
        for g in letters:
            v = groups_data.get(g) or {}
            if hide_zero and not _nonzero(v.get("total"), v.get("ht"), v.get("vat"), v.get("ts")):
                continue
            groups.append({"group": g, "total": _num(v.get("total")), "ht": _num(v.get("ht")),
                           "vat": _num(v.get("vat")), "ts": _num(v.get("ts"))})
        if hide_zero and not groups and not _nonzero(s.get("count"), s.get("incomplete"), s.get("total"),
                                                    s.get("ht"), s.get("vat"), s.get("ts")):
            continue
        types.append({"type": t, "count": int(s.get("count") or 0), "incomplete": int(s.get("incomplete") or 0),
                      "total": _num(s.get("total")), "ht": _num(s.get("ht")), "vat": _num(s.get("vat")),
                      "ts": _num(s.get("ts")), "groups": groups})
    red = data.get("reductions") or {}
    return {
        "types": types,
        "payments": [{"label": p.get("label") or p.get("type"), "amount": _num(p.get("net"))}
                     for p in data.get("payments") or []
                     if not hide_zero or _nonzero(p.get("sales"), p.get("credits"), p.get("net"))],
        "reductions": [{"type": t, "amount": _num((red.get(t) or {}).get("amount")),
                        "tax": _num((red.get(t) or {}).get("tax"))} for t in fr.INVOICE_TYPES
                       if not hide_zero or _nonzero((red.get(t) or {}).get("amount"), (red.get(t) or {}).get("tax"))],
        "natures": [{"code": n.get("code"), "label": n.get("label"), "count": int(n.get("count") or 0),
                     "total": _num(n.get("total"))} for n in data.get("natures") or []
                    if not hide_zero or _nonzero(n.get("count"), n.get("total"))],
    }


def _identity(pv):
    parts = [("Code magasin", pv.get("code")), ("NIM e-MCF", pv.get("nim")), ("MCF (NID)", pv.get("nid")),
             ("Ville", pv.get("city"))]
    return " – ".join(f"{k} {v}" for k, v in parts if v)


def report_view(doc, hide_zero=None):
    """Rows of the printed report, already formatted (layout of the DGI e-UF reports, SFE 1.3.3 / 1.4.2).

    v5.4.2 - "blocks": one block per point de vente fiscal, then the synthesis of all of them. With a single
    point de vente (or a report generated before v5.4.2) there is ONE block and no synthesis. The other keys
    are the complete rows of the whole company (every type, every group), as before."""
    data = doc.get_data()
    kind = CODES.get(doc.report_type, "X")
    hide_zero = hide_zero_rows() if hide_zero is None else hide_zero
    make = _a_block if kind == "A" else (lambda d: _xz_block(d, hide_zero))
    view = _a_block(data) if kind == "A" else _xz_block(data, False)
    pvs = data.get("points_of_sale") or []
    if len(pvs) > 1:
        blocks = [dict(make(pv), heading=f"Point de vente : {pv.get('label') or pv.get('point_of_sale')}",
                       identity=_identity(pv), synthesis=False) for pv in pvs]
        blocks.append(dict(make(data), heading=f"Synthèse : tous les points de vente réunis ({len(pvs)})",
                           identity="", synthesis=True))
        view["point_of_sale"] = ""
    else:
        blocks = [dict(make(data), heading="", identity="", synthesis=False)]
        pv = pvs[0] if pvs and pvs[0].get("point_of_sale") else {}
        view["point_of_sale"] = " – ".join(x for x in (pv.get("label"), _identity(pv)) if x)
    view["blocks"] = blocks
    view["hide_zero"] = hide_zero
    return view


def dgi_report_context(doc):
    """Jinja helper for the print format."""
    if isinstance(doc, str):
        doc = frappe.get_doc("DGI Rapport Fiscal", doc)
    fmt = "%d/%m/%Y %H:%M:%S"
    def _dt(v):
        return get_datetime(v).strftime(fmt) if v else ""
    from dgi_compliance import __version__
    kind = CODES.get(doc.report_type, "X")
    if kind == "Z":
        title = f"Z rapport #{doc.sequence_no}"
    elif kind == "A":
        title = f"A rapport #{doc.sequence_no}"
    elif doc.report_type == "X périodique":
        title = "X rapport périodique"
    else:
        title = "X rapport quotidien"
    return {"app_version": __version__, "data": doc.get_data(), "view": report_view(doc), "title": title,
            "groups": fr.GROUPS, "types": fr.INVOICE_TYPES, "kind": kind,
            "generated_on": _dt(doc.generated_on), "from": _dt(doc.from_datetime), "to": _dt(doc.to_datetime),
            "generated_by": frappe.utils.get_fullname(doc.generated_by) or doc.generated_by}
