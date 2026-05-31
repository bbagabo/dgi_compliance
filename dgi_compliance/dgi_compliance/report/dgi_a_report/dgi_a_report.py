"""
DGI A Report -- "rapport periodique / d'audit".

Aggregated fiscal report over an arbitrary date range, one row per day, with
per-day HT / VAT / TTC and document counts. Intended for periodic (e.g.
monthly) reconciliation and DGI audit, complementing the daily Z reports.
"""

from __future__ import annotations

import frappe
from frappe.utils import nowdate, get_first_day

from dgi_compliance.dgi_compliance.report.dgi_report_utils import base_conditions


def execute(filters=None):
    filters = filters or {}
    from_date = filters.get("from_date") or str(get_first_day(nowdate()))
    to_date = filters.get("to_date") or nowdate()

    where, params = base_conditions(filters)
    params["from_date"] = from_date
    params["to_date"] = to_date
    date_clause = "si.posting_date BETWEEN %(from_date)s AND %(to_date)s"

    data = frappe.db.sql(
        f"""
        SELECT si.posting_date AS day,
               SUM(CASE WHEN si.dgi_invoice_type IN ('FV','EV') THEN 1 ELSE 0 END) AS sales,
               SUM(CASE WHEN si.dgi_invoice_type IN ('FA','EA') THEN 1 ELSE 0 END) AS credits,
               SUM(si.net_total)               AS ht,
               SUM(si.total_taxes_and_charges) AS vat,
               SUM(si.grand_total)             AS ttc
        FROM `tabSales Invoice` si
        WHERE {where} AND {date_clause}
        GROUP BY si.posting_date
        ORDER BY si.posting_date
        """,
        params, as_dict=True,
    )

    columns = [
        {"label": "Jour", "fieldname": "day", "fieldtype": "Date", "width": 110},
        {"label": "Ventes", "fieldname": "sales", "fieldtype": "Int", "width": 80},
        {"label": "Avoirs", "fieldname": "credits", "fieldtype": "Int", "width": 80},
        {"label": "Total HT", "fieldname": "ht", "fieldtype": "Float", "width": 150},
        {"label": "Total TVA", "fieldname": "vat", "fieldtype": "Float", "width": 150},
        {"label": "Total TTC", "fieldname": "ttc", "fieldtype": "Float", "width": 160},
    ]

    report_summary = [
        {"label": "Periode", "value": f"{from_date} -> {to_date}", "datatype": "Data"},
        {"label": "Jours", "value": len(data), "datatype": "Int"},
        {"label": "Total HT", "value": sum(float(r.ht or 0) for r in data), "datatype": "Float"},
        {"label": "Total TVA", "value": sum(float(r.vat or 0) for r in data), "datatype": "Float"},
        {"label": "Total TTC", "value": sum(float(r.ttc or 0) for r in data), "datatype": "Float", "indicator": "Green"},
    ]

    return columns, data, None, None, report_summary
