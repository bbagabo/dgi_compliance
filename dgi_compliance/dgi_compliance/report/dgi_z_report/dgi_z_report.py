"""
DGI Z Report -- "cloture journaliere" (daily close).

End-of-day fiscal report for a POS: cumulative totals for the fiscal day,
broken down by DGI tax group, plus invoice/credit-note counts and the first
and last DEF/DGI codes of the day. In a software ERP there is no hardware
counter to physically reset; the Z report is the authoritative daily summary
to be archived for the DGI.
"""

from __future__ import annotations

import frappe
from frappe.utils import nowdate

from dgi_compliance.dgi_compliance.report.dgi_report_utils import (
    base_conditions, tax_group_breakdown,
)


def execute(filters=None):
    filters = filters or {}
    date = filters.get("date") or nowdate()
    date_clause = "si.posting_date = %(date)s"

    # Per-tax-group HT / VAT / TTC for the day.
    data = tax_group_breakdown(filters, date_clause, {"date": date})

    columns = [
        {"label": "Groupe TVA", "fieldname": "tax_group", "fieldtype": "Data", "width": 120},
        {"label": "Base HT", "fieldname": "ht", "fieldtype": "Float", "width": 160},
        {"label": "TVA", "fieldname": "vat", "fieldtype": "Float", "width": 160},
        {"label": "TTC", "fieldname": "ttc", "fieldtype": "Float", "width": 160},
    ]

    # Counts + first/last DEF codes.
    where, params = base_conditions(filters)
    params["date"] = date
    summary = frappe.db.sql(
        f"""
        SELECT
          SUM(CASE WHEN si.dgi_invoice_type IN ('FV','EV') THEN 1 ELSE 0 END) AS sales,
          SUM(CASE WHEN si.dgi_invoice_type IN ('FA','EA') THEN 1 ELSE 0 END) AS credits,
          COUNT(*)            AS total_docs,
          MIN(si.dgi_code_def) AS first_def,
          MAX(si.dgi_code_def) AS last_def,
          SUM(si.net_total)   AS ht,
          SUM(si.total_taxes_and_charges) AS vat,
          SUM(si.grand_total) AS ttc
        FROM `tabSales Invoice` si
        WHERE {where} AND {date_clause}
        """,
        params, as_dict=True,
    )
    s = summary[0] if summary else {}

    report_summary = [
        {"label": "Date de cloture", "value": str(date), "datatype": "Data"},
        {"label": "Ventes (FV/EV)", "value": int(s.get("sales") or 0), "datatype": "Int"},
        {"label": "Avoirs (FA/EA)", "value": int(s.get("credits") or 0), "datatype": "Int"},
        {"label": "Documents certifies", "value": int(s.get("total_docs") or 0), "datatype": "Int"},
        {"label": "Total HT", "value": float(s.get("ht") or 0), "datatype": "Float"},
        {"label": "Total TVA", "value": float(s.get("vat") or 0), "datatype": "Float"},
        {"label": "Total TTC", "value": float(s.get("ttc") or 0), "datatype": "Float", "indicator": "Green"},
        {"label": "Premier code DEF", "value": s.get("first_def") or "-", "datatype": "Data"},
        {"label": "Dernier code DEF", "value": s.get("last_def") or "-", "datatype": "Data"},
    ]

    message = f"<b>Rapport Z -- Cloture du {date}</b> pour POS: {filters.get('pos') or 'Tous'}"
    return columns, data, message, None, report_summary
