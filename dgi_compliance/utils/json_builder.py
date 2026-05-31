"""
Build the JSON body the DGI /api/invoice endpoint expects.

The exact field names align with the patterns observed in the Business
Central reference implementation (HeloSystems DGI Exchange Module v1.0.0.0)
and the DGI e-DEF public documentation. Adjust here — and only here — if
the DGI publishes a new spec; the rest of the code is decoupled.
"""

from __future__ import annotations

import frappe

from dgi_compliance.utils import mapping


def build_invoice_request_body(sales_invoice, pos_code: str,
                               *, cancel: bool = False,
                               reason: str = "") -> dict:
    """Return the dict that will be `json.dumps`-ed into the POST body."""
    customer = frappe.get_doc("Customer", sales_invoice.customer)
    pos = frappe.get_doc("DGI eMCF POS", pos_code)

    body: dict = {
        "uid": sales_invoice.get("dgi_uid"),
        "nim": pos.nim,
        "operation": "CANCEL" if cancel else "SEND",
        "invoiceType": mapping.map_invoice_type(sales_invoice),
        "documentNumber": sales_invoice.name,
        "issueDate": str(sales_invoice.posting_date),
        "currency": sales_invoice.currency,
        "currencyRate": float(sales_invoice.conversion_rate or 1.0),
        "totalNet": float(sales_invoice.net_total or 0.0),
        "totalVAT": float(sales_invoice.total_taxes_and_charges or 0.0),
        "totalIncludingVAT": float(sales_invoice.grand_total or 0.0),
        "paymentType": mapping.map_payment_type(sales_invoice),
        "customer": _customer_payload(customer, sales_invoice),
        "items": [_item_payload(row) for row in sales_invoice.items],
        "taxes": _tax_breakdown(sales_invoice),
    }

    if cancel:
        body["referenceDgiCode"] = sales_invoice.get("dgi_code_def")
        body["cancelReason"] = reason or sales_invoice.get("dgi_reference_desc") or ""

    if sales_invoice.get("is_return") and sales_invoice.get("return_against"):
        original = frappe.db.get_value(
            "Sales Invoice", sales_invoice.return_against,
            ["dgi_code_def", "dgi_uid"], as_dict=True,
        ) or {}
        body["referenceDgiCode"] = original.get("dgi_code_def")
        body["referenceUid"] = original.get("dgi_uid")
        body["referenceType"] = sales_invoice.get("dgi_reference_type") or "ANN"
        body["referenceDescription"] = sales_invoice.get("dgi_reference_desc") or ""

    return body


def _customer_payload(customer, sales_invoice) -> dict:
    return {
        "name": customer.customer_name,
        "type": mapping.map_client_type(customer),
        "nif": customer.tax_id or "",
        "address": sales_invoice.get("customer_address_display") or
                   customer.get("primary_address") or "",
    }


def _item_payload(item_row) -> dict:
    return {
        "code": item_row.item_code,
        "name": item_row.item_name,
        "type": mapping.map_item_type(item_row),
        "taxGroup": mapping.map_tax_group(item_row.get("item_tax_template")),
        "quantity": float(item_row.qty or 0.0),
        "uom": item_row.uom,
        "unitPrice": float(item_row.rate or 0.0),
        "discountPct": float(item_row.discount_percentage or 0.0),
        "netAmount": float(item_row.net_amount or 0.0),
        "totalAmount": float(item_row.amount or 0.0),
    }


def _tax_breakdown(sales_invoice) -> list[dict]:
    """Group invoice taxes by DGI tax-group code (A–P)."""
    aggregated: dict[str, dict] = {}
    for item in sales_invoice.items:
        group = mapping.map_tax_group(item.get("item_tax_template"))
        bucket = aggregated.setdefault(group, {
            "taxGroup": group,
            "netAmount": 0.0,
            "vatAmount": 0.0,
            "amountInclVAT": 0.0,
        })
        bucket["netAmount"] += float(item.net_amount or 0.0)
        bucket["amountInclVAT"] += float(item.amount or 0.0)
    # VAT amount is the difference (avoid double-counting if line totals
    # already include VAT — ERPNext stores them separately).
    total_tax = float(sales_invoice.total_taxes_and_charges or 0.0)
    total_net = sum(b["netAmount"] for b in aggregated.values()) or 1.0
    for bucket in aggregated.values():
        bucket["vatAmount"] = round(total_tax * bucket["netAmount"] / total_net, 2)
        bucket["amountInclVAT"] = round(bucket["netAmount"] + bucket["vatAmount"], 2)
    return list(aggregated.values())
