"""Shared helpers for the DGI fiscal reports (A / X / Z)."""

from __future__ import annotations

import frappe


def base_conditions(filters: dict, alias: str = "si") -> tuple[str, dict]:
    """Build the common WHERE clause: submitted + certified Sales Invoices."""
    conds = [
        f"{alias}.docstatus = 1",
        f"IFNULL({alias}.dgi_code_def, '') != ''",
    ]
    params: dict = {}
    if filters.get("pos"):
        conds.append(f"{alias}.dgi_emcf_pos = %(pos)s")
        params["pos"] = filters["pos"]
    return " AND ".join(conds), params


def tax_group_breakdown(filters: dict, date_clause: str, params: dict) -> list[dict]:
    """Aggregate net (HT) by DGI tax group and allocate VAT proportionally.

    VAT cannot be read per group directly from ERPNext, so we split the
    invoice-level total_taxes_and_charges across groups in proportion to net.
    """
    where, p = base_conditions(filters)
    p.update(params)
    rows = frappe.db.sql(
        f"""
        SELECT IFNULL(sii.dgi_tax_group, 'A') AS tax_group,
               SUM(sii.net_amount)            AS ht
        FROM `tabSales Invoice Item` sii
        INNER JOIN `tabSales Invoice` si ON si.name = sii.parent
        WHERE {where} AND {date_clause}
        GROUP BY IFNULL(sii.dgi_tax_group, 'A')
        ORDER BY tax_group
        """,
        p, as_dict=True,
    )
    total_vat = frappe.db.sql(
        f"""
        SELECT SUM(si.total_taxes_and_charges) AS vat,
               SUM(si.net_total)               AS ht
        FROM `tabSales Invoice` si
        WHERE {where} AND {date_clause}
        """,
        p, as_dict=True,
    )
    vat = float((total_vat[0].vat if total_vat else 0) or 0)
    ht_total = float((total_vat[0].ht if total_vat else 0) or 0) or 1.0
    for r in rows:
        r["ht"] = float(r["ht"] or 0)
        r["vat"] = round(vat * r["ht"] / ht_total, 2)
        r["ttc"] = round(r["ht"] + r["vat"], 2)
    return rows
