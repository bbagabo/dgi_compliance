"""
DGI X Report -- "lecture X".

An intermediate fiscal read of the current day's certified sales for a POS,
WITHOUT closing the day or resetting any counter. Lists every certified
Sales Invoice of the selected date and shows running totals. Safe to run as
many times as needed during the day.
"""

from __future__ import annotations

import frappe
from frappe.utils import nowdate

from dgi_compliance.dgi_compliance.report.dgi_report_utils import base_conditions


def execute(filters=None):
    filters = filters or {}
    date = filters.get("date") or nowdate()

    where, params = base_conditions(filters)
    params["date"] = date

    data = frappe.db.sql(
        f"""
        SELECT si.name             AS invoice,
               si.posting_time     AS time,
               si.customer_name    AS customer,
               si.dgi_invoice_type AS type,
               si.net_total        AS ht,
               si.total_taxes_and_charges AS vat,
               si.grand_total      AS ttc,
               si.currency         AS currency,
               si.dgi_code_def     AS def_code,
               si.dgi_status       AS status
        FROM `tabSales Invoice` si
        WHERE {where} AND si.posting_date = %(date)s
        ORDER BY si.posting_time
        """,
        params, as_dict=True,
    )

    columns = [
        {"label": "Facture", "fieldname": "invoice", "fieldtype": "Link", "options": "Sales Invoice", "width": 150},
        {"label": "Heure", "fieldname": "time", "fieldtype": "Time", "width": 90},
        {"label": "Client", "fieldname": "customer", "fieldtype": "Data", "width": 180},
        {"label": "Type", "fieldname": "type", "fieldtype": "Data", "width": 60},
        {"label": "HT", "fieldname": "ht", "fieldtype": "Float", "width": 110},
        {"label": "TVA", "fieldname": "vat", "fieldtype": "Float", "width": 110},
        {"label": "TTC", "fieldname": "ttc", "fieldtype": "Float", "width": 120},
        {"label": "Devise", "fieldname": "currency", "fieldtype": "Data", "width": 70},
        {"label": "Code DEF/DGI", "fieldname": "def_code", "fieldtype": "Data", "width": 160},
        {"label": "Statut", "fieldname": "status", "fieldtype": "Data", "width": 90},
    ]

    n = len(data)
    ht = sum(float(r.ht or 0) for r in data)
    vat = sum(float(r.vat or 0) for r in data)
    ttc = sum(float(r.ttc or 0) for r in data)
    report_summary = [
        {"label": "Date", "value": str(date), "datatype": "Data"},
        {"label": "Factures certifiees", "value": n, "datatype": "Int"},
        {"label": "Total HT", "value": ht, "datatype": "Float"},
        {"label": "Total TVA", "value": vat, "datatype": "Float"},
        {"label": "Total TTC", "value": ttc, "datatype": "Float", "indicator": "Green"},
    ]

    return columns, data, None, None, report_summary
