"""
ERPNext ↔ DGI value mapping helpers.

Kept deliberately small and stateless so they're easy to unit-test.
"""

from __future__ import annotations

import frappe


# ---------------------------------------------------------------------------
# POS resolution
# ---------------------------------------------------------------------------

def resolve_pos_for_invoice(sales_invoice) -> str | None:
    """Resolve the DGI eMCF POS code applicable to a Sales Invoice.

    Resolution order:
      1. Explicit selection on the invoice (custom field `dgi_emcf_pos`)
      2. POS Profile → linked DGI eMCF POS (custom field on POS Profile)
      3. Warehouse → linked DGI eMCF POS (custom field on Warehouse)
      4. Single default in DGI Settings
    """
    if sales_invoice.get("dgi_emcf_pos"):
        return sales_invoice.dgi_emcf_pos

    if sales_invoice.get("pos_profile"):
        pos = frappe.db.get_value("POS Profile", sales_invoice.pos_profile,
                                  "dgi_emcf_pos")
        if pos:
            return pos

    if sales_invoice.get("set_warehouse"):
        pos = frappe.db.get_value("Warehouse", sales_invoice.set_warehouse,
                                  "dgi_emcf_pos")
        if pos:
            return pos

    settings = frappe.get_single("DGI Settings")
    return settings.default_pos or None


# ---------------------------------------------------------------------------
# Field-level mappers
# ---------------------------------------------------------------------------

# DGI tax group codes (A–P) keyed by ERPNext Item Tax Template name.
# This is normally populated by the user through DGI Tax Group Mapping,
# but the helper resolves a sane default when a row is missing.
DEFAULT_TAX_GROUP = "A"


def map_tax_group(item_tax_template: str | None) -> str:
    if not item_tax_template:
        return DEFAULT_TAX_GROUP
    mapped = frappe.db.get_value("DGI Tax Group Mapping",
                                 {"item_tax_template": item_tax_template},
                                 "dgi_tax_group")
    return mapped or DEFAULT_TAX_GROUP


def map_client_type(customer_doc) -> str:
    return customer_doc.get("dgi_client_type") or "PP"


def map_item_type(item_row) -> str:
    """Sales Invoice Item row → DGI item type code."""
    return item_row.get("dgi_item_type") or "VAR"


def map_payment_type(sales_invoice) -> str:
    """A Sales Invoice can carry multiple payments; for DGI we pick the
    first non-empty mode_of_payment and resolve it through DGI Reference Data."""
    if not sales_invoice.get("payments"):
        return sales_invoice.get("dgi_payment_type") or "ESPECES"
    for p in sales_invoice.payments:
        if p.mode_of_payment:
            row = frappe.db.get_value(
                "Mode of Payment",
                p.mode_of_payment,
                "dgi_payment_type",
            )
            if row:
                return row
    return sales_invoice.get("dgi_payment_type") or "ESPECES"


def map_invoice_type(sales_invoice) -> str:
    """Derive DGI Invoice Type from the Sales Invoice flags + user override.

    Defaults:
        is_return = 0 → FV (Facture de vente)
        is_return = 1 → FA (Facture d'avoir)
        is_pos    = 1 → FV (counter), but Custom field overrides
    """
    explicit = sales_invoice.get("dgi_invoice_type")
    if explicit:
        return explicit
    if sales_invoice.get("is_return"):
        return sales_invoice.get("dgi_credit_note_type") or "FA"
    return "FV"


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

def redact_token(headers: dict) -> dict:
    """Replace any Authorization header value with '***' before logging."""
    out = dict(headers)
    if "Authorization" in out:
        out["Authorization"] = "Bearer ***"
    return out
