"""DGI Rapport Fiscal: X (quotidien / periodique), Z and A statistical reports, frozen at creation.

A report can neither be modified nor deleted: it is part of the electronic journal (SFE 1.1.20).
"""
import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_datetime, now_datetime

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


def dgi_report_context(doc):
    """Jinja helper for the print format."""
    if isinstance(doc, str):
        doc = frappe.get_doc("DGI Rapport Fiscal", doc)
    fmt = "%d/%m/%Y %H:%M:%S"
    def _dt(v):
        return get_datetime(v).strftime(fmt) if v else ""
    from dgi_compliance import __version__
    return {"app_version": __version__, "data": doc.get_data(), "groups": fr.GROUPS, "types": fr.INVOICE_TYPES,
            "kind": CODES.get(doc.report_type, "X"),
            "generated_on": _dt(doc.generated_on), "from": _dt(doc.from_datetime), "to": _dt(doc.to_datetime),
            "generated_by": frappe.utils.get_fullname(doc.generated_by) or doc.generated_by}
