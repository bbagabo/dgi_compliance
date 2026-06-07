"""Ceiling rounding of VAT on the ERPNext Sales Invoice (upgrade-safe, hook-based).

ERPNext has NO native 'always round up' option (only Banker's / Commercial rounding, both
round-to-nearest). This module adds an optional ceiling rounding, applied ONLY to the tax rows
whose account_head is listed in DGI Compliance Settings -> VAT Accounts, with a configurable
number of decimals. It is OFF by default and only runs when Settings.enabled and vat_round_up.

GL stays balanced because the delta is added to BOTH the tax row (VAT payable) and the grand
total (receivable). Test in staging before enabling in production.
"""
from decimal import Decimal, ROUND_CEILING
import frappe
from frappe.utils import flt, rounded
from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings


def round_up(value, decimals: int = 0) -> float:
    """Round the MAGNITUDE up (toward larger absolute value), preserving sign.
    round_up(1240.2, 0) -> 1241 ; round_up(12.341, 2) -> 12.35 ; round_up(-12.2, 0) -> -13."""
    if value in (None, ""):
        return 0.0
    v = Decimal(str(abs(float(value))))
    q = Decimal(1).scaleb(-int(decimals))  # 10^-decimals
    up = v.quantize(q, rounding=ROUND_CEILING)
    out = float(up)
    return -out if float(value) < 0 else out


def _vat_accounts(settings):
    return {r.account_head for r in (settings.vat_accounts or []) if r.account_head}


def doc_vat_base(doc, settings=None) -> float:
    """Sum of base-currency tax amounts for the configured VAT accounts (post-rounding)."""
    settings = settings or get_settings()
    accs = _vat_accounts(settings)
    if not accs:
        return 0.0
    return flt(sum(flt(r.base_tax_amount) for r in (doc.get("taxes") or []) if r.account_head in accs))


def apply_vat_ceiling(doc, method=None):
    """Sales Invoice `validate` hook: ceil the VAT tax rows and reconcile the document totals."""
    settings = get_settings()
    if not settings.enabled or not settings.vat_round_up:
        return
    accs = _vat_accounts(settings)
    if not accs:
        return
    decimals = int(settings.vat_round_precision or 0)
    rate = flt(doc.conversion_rate) or 1.0
    delta = 0.0  # in transaction currency

    for row in (doc.get("taxes") or []):
        if row.account_head not in accs:
            continue
        old = flt(row.tax_amount)
        new = round_up(old, decimals)
        if new == old:
            continue
        diff = new - old
        row.tax_amount = new
        row.base_tax_amount = flt(new * rate)
        # keep the after-discount mirrors consistent if present
        if hasattr(row, "tax_amount_after_discount_amount"):
            row.tax_amount_after_discount_amount = new
            row.base_tax_amount_after_discount_amount = flt(new * rate)
        row.total = flt(flt(row.total) + diff)
        row.base_total = flt(flt(row.base_total) + diff * rate)
        delta += diff

    if not delta:
        return

    doc.total_taxes_and_charges = flt(flt(doc.total_taxes_and_charges) + delta)
    doc.base_total_taxes_and_charges = flt(flt(doc.base_total_taxes_and_charges) + delta * rate)
    doc.grand_total = flt(flt(doc.grand_total) + delta)
    doc.base_grand_total = flt(flt(doc.base_grand_total) + delta * rate)

    # Rounded total honouring System Settings (unless disabled on the doc)
    if not doc.get("disable_rounded_total"):
        doc.rounded_total = rounded(doc.grand_total, doc.precision("rounded_total"))
        doc.base_rounded_total = rounded(doc.base_grand_total, doc.precision("base_rounded_total"))
        doc.rounding_adjustment = flt(doc.rounded_total - doc.grand_total)
        doc.base_rounding_adjustment = flt(doc.base_rounded_total - doc.base_grand_total)

    # Outstanding (draft): keep it aligned with the new grand total
    if doc.docstatus == 0:
        payable = doc.rounded_total or doc.grand_total
        doc.outstanding_amount = flt(payable - flt(doc.get("total_advance")) - flt(doc.get("paid_amount")) - flt(doc.get("write_off_amount")))
