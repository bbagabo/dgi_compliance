"""Single source of truth for the payment modes of a Sales Invoice.

The SAME lines are sent to the e-MCF (PaymentDto) and printed on the normalized invoice, so
the printed data always equals the exchanged data (SFE 1.1.4) and the invoice total always
equals the sum of the payment lines (SFE 1.1.19).

Resolution order (the default is used ONLY when nothing was selected on the invoice):
  1. POS payment rows (Sales Invoice Payment) with a non-zero amount; change returned is
     deducted from the cash row; an unpaid remainder is declared as CREDIT;
  2. the "Mode de paiement" explicitly selected on the invoice (custom field
     dgi_mode_of_payment) for the full amount;
  3. the modes of payment of the payment schedule (payment terms);
  4. the Default Payment Type of DGI Compliance Settings.
A selected mode that has no Payment Mode Mapping blocks with an explicit message (v3.6 rule).
"""
import frappe
from frappe import _
from frappe.utils import flt

from dgi_compliance.edef.calc import round2

PAYMENT_LABELS = {
    "ESPECES": "Espèces",
    "VIREMENT": "Virement",
    "CARTEBANCAIRE": "Carte bancaire",
    "MOBILEMONEY": "Mobile money",
    "CHEQUES": "Chèque",
    "CREDIT": "Crédit",
    "AUTRE": "Autre",
}


class PaymentTypeMappingError(frappe.ValidationError):
    """A Mode of Payment is specified on the invoice but has no Payment Type mapping."""


def _map(settings, mode, idx=None):
    ptype = settings.payment_type_for(mode, strict=True)
    if not ptype:
        frappe.throw(
            _("Mapping Payment Type manquant pour le mode de paiement '{0}'{1}. Ajoutez la "
              "correspondance dans DGI Compliance Settings > Payment Mode Mapping. Le Default "
              "Payment Type ne s'applique que lorsqu'aucun mode de paiement n'est choisi sur la "
              "facture.").format(mode, _(" (ligne #{0})").format(idx) if idx else ""),
            exc=PaymentTypeMappingError, title=_("Mapping Payment Type DGI"))
    return ptype


def _line(ptype, mode, amount_txn, amount_cdf, source):
    return {
        "type": ptype,
        "mode_of_payment": mode or "",
        "label": mode or PAYMENT_LABELS.get(ptype, ptype),
        "type_label": PAYMENT_LABELS.get(ptype, ptype),
        "amount": round2(abs(flt(amount_txn))),
        "amount_cdf": round2(abs(flt(amount_cdf))),
        "source": source,
    }


def _merge(lines):
    out = {}
    for ln in lines:
        key = (ln["type"], ln["mode_of_payment"])
        if key in out:
            out[key]["amount"] = round2(out[key]["amount"] + ln["amount"])
            out[key]["amount_cdf"] = round2(out[key]["amount_cdf"] + ln["amount_cdf"])
        else:
            out[key] = dict(ln)
    return [ln for ln in out.values() if ln["amount_cdf"] or ln["amount"]]


def invoice_totals(doc):
    """(total in invoice currency, total in CDF) declared to the e-MCF: the grand total before
    cash rounding (the e-MCF recomputes the unrounded total; SFE 1.1.19)."""
    return abs(flt(doc.get("grand_total"))), abs(flt(doc.get("base_grand_total")))


MAX_ROUNDING_GAP = 1.0  # CDF


def align_to_fiscal_total(rows, total, key="amount", max_gap=MAX_ROUNDING_GAP):
    """SFE 1.1.19 (total = sum of the payments) on the FISCAL total.

    ERPNext rounds VAT to the nearest centime; the e-MCF rounds it up (SFE 1.5). With an exchange
    rate the CDF amounts rarely fall on round figures, so the ERPNext total can differ from the
    fiscal total by a few centimes. Such a rounding gap (<= max_gap) is absorbed by the CREDIT line
    if there is one, otherwise by the largest line. A larger gap is left untouched: it is a real
    difference, reported by the amount check before CONFIRM."""
    if not rows:
        return rows
    gap = round2(flt(total) - sum(flt(r.get(key)) for r in rows))
    if abs(gap) < 0.005 or abs(gap) > max_gap:
        return rows
    target = next((r for r in rows if (r.get("name") or r.get("type")) == "CREDIT"), None) \
        or max(rows, key=lambda r: flt(r.get(key)))
    target[key] = round2(flt(target.get(key)) + gap)
    return rows


def _adjust_to_total(lines, total_txn, total_cdf, settings, source):
    """Make the lines add up to the invoice total: change deducted, remainder as CREDIT."""
    paid_txn = sum(ln["amount"] for ln in lines)
    paid_cdf = sum(ln["amount_cdf"] for ln in lines)
    diff_cdf = round2(total_cdf - paid_cdf)
    diff_txn = round2(total_txn - paid_txn)
    if abs(diff_cdf) < 0.01:
        return lines
    if diff_cdf > 0:
        lines.append(_line("CREDIT", "", diff_txn, diff_cdf, source + "-solde"))
        return lines
    # Overpaid (change returned): reduce the cash line first, then the last line.
    target = next((ln for ln in lines if ln["type"] == "ESPECES"), lines[-1])
    target["amount"] = round2(target["amount"] + diff_txn)
    target["amount_cdf"] = round2(target["amount_cdf"] + diff_cdf)
    return [ln for ln in lines if ln["amount_cdf"] > 0]


def resolve_payment_lines(doc, settings=None):
    """List of payment lines {type, mode_of_payment, label, type_label, amount, amount_cdf,
    source}. Amounts are positive magnitudes (credit notes are negative in ERPNext)."""
    if settings is None:
        from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings
        settings = get_settings()
    total_txn, total_cdf = invoice_totals(doc)
    rate = flt(doc.get("conversion_rate")) or 1.0

    # 1. POS payments
    pos_rows = [p for p in (doc.get("payments") or []) if flt(p.get("amount")) or flt(p.get("base_amount"))]
    if pos_rows:
        lines = []
        for p in pos_rows:
            ptype = _map(settings, p.get("mode_of_payment"), p.get("idx"))
            base = flt(p.get("base_amount")) or flt(p.get("amount")) * rate
            lines.append(_line(ptype, p.get("mode_of_payment"), p.get("amount"), base, "pos"))
        return _merge(_adjust_to_total(_merge(lines), total_txn, total_cdf, settings, "pos"))

    # 2. Mode explicitly selected on the invoice
    mode = doc.get("dgi_mode_of_payment")
    if mode:
        return [_line(_map(settings, mode), mode, total_txn, total_cdf, "invoice")]

    # 3. Payment schedule (payment terms) modes
    sched = [s for s in (doc.get("payment_schedule") or []) if s.get("mode_of_payment")]
    if sched:
        lines = []
        for s in sched:
            ptype = _map(settings, s.get("mode_of_payment"), s.get("idx"))
            amt = flt(s.get("payment_amount"))
            base = flt(s.get("base_payment_amount")) or amt * rate
            lines.append(_line(ptype, s.get("mode_of_payment"), amt, base, "schedule"))
        return _merge(_adjust_to_total(_merge(lines), total_txn, total_cdf, settings, "schedule"))

    # 4. Default payment type (nothing selected on the invoice)
    ptype = settings.default_payment_type or "ESPECES"
    return [_line(ptype, "", total_txn, total_cdf, "default")]
