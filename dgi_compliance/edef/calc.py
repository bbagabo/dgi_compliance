"""Pure fiscal arithmetic (no Frappe import): rounding, specific tax, totals per tax group.

Implements the SFE calculation rules (Specifications SFE fev. 2026, section 1.5):
  1.5.1 prices up to 2 decimals, quantities up to 3 decimals;
  1.5.2 every decimal value rounded to the nearest, max 2 decimals;
  1.5.6 TTC mode: taxable amount derived from the group total; HT mode: total derived from
        the group taxable amount;
  1.5.7 taxable + tax = total; when rounding, the TAX is rounded UP to satisfy the equality;
  1.5.8 the amount recorded in group N is entirely VAT;
  1.5.10 the specific tax increases the VAT base.

The e-MCF recomputes every amount; these functions let the SFE check the e-MCF figures
before CONFIRM (API e-MCF v1.3, workflow p.5) and rebuild a breakdown when no e-MCF
response is stored (invoices normalized before v5.0.0).
"""
from decimal import Decimal, ROUND_HALF_UP, ROUND_CEILING

VAT_ONLY_GROUPS = ("N",)  # SFE 1.5.8


def _d(value) -> Decimal:
    return Decimal(str(value or 0))


def round2(value) -> float:
    return float(_d(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def round3(value) -> float:
    return float(_d(value).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


def ceil2(value) -> float:
    """Round the magnitude UP to 2 decimals (sign preserved)."""
    v = _d(value)
    up = abs(v).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
    return float(-up if v < 0 else up)


def parse_specific_tax(value):
    """'230' -> ('unit', 230.0) ; '10%' -> ('percent', 10.0) ; '' -> (None, 0.0).
    Raises ValueError on an unreadable value (API error 21/26)."""
    s = str(value or "").strip().replace(",", ".").replace(" ", "")
    if not s:
        return None, 0.0
    if s.endswith("%"):
        n = float(s[:-1])
        if n < 0:
            raise ValueError("taxe specifique negative")
        return "percent", n
    n = float(s)
    if n < 0:
        raise ValueError("taxe specifique negative")
    return "unit", n


def format_specific_tax(kind, number) -> str:
    if kind is None:
        return ""
    txt = ("%.4f" % number).rstrip("0").rstrip(".")
    return txt + "%" if kind == "percent" else txt


def split_specific_tax(net_amount_excl_vat, qty, value):
    """The specific tax is carried INSIDE the ERPNext line net amount (VAT base = HT + TS,
    SFE 1.5.10). Returns (amount_without_ts, ts_amount, ts_value_str)."""
    kind, number = parse_specific_tax(value)
    net = float(net_amount_excl_vat or 0)
    if kind is None or not number:
        return net, 0.0, ""
    if kind == "unit":
        ts = round2(number * float(qty or 0))
    else:
        ts = round2(net - net / (1 + number / 100.0))
    return net - ts, ts, format_specific_tax(kind, number)


def group_amounts(total_or_base, rate, mode, group=None):
    """Split one group's amount into (total, ht, vat) following SFE 1.5.6/1.5.7."""
    amount = round2(total_or_base)
    r = float(rate or 0)
    if group in VAT_ONLY_GROUPS:
        return amount, 0.0, amount
    if not r:
        return amount, amount, 0.0
    if (mode or "ttc").lower() == "ttc":
        vat = ceil2(float(_d(amount) * _d(r) / (_d(100) + _d(r))))
        return amount, round2(amount - vat), vat
    vat = ceil2(float(_d(amount) * _d(r) / _d(100)))
    return round2(amount + vat), amount, vat


def compute_totals(items, mode, rates):
    """Recompute the invoice the way the e-MCF does, from the request items.

    items: dicts with price, quantity, taxGroup and optional taxSpecificAmount.
    rates: {group letter: rate %} (taken from the e-MCF response when available).
    Returns {"groups": {L: {"total", "ht", "vat", "ts", "rate"}}, "total", "vtotal", "ts"}.
    """
    mode = (mode or "ttc").lower()
    acc = {}
    for it in items or []:
        g = str(it.get("taxGroup") or "").upper()
        a = acc.setdefault(g, {"amount": Decimal("0"), "ts": Decimal("0")})
        a["amount"] += _d(round2(_d(it.get("price")) * _d(it.get("quantity"))))
        a["ts"] += _d(round2(it.get("taxSpecificAmount") or 0))
    groups, total, vtotal, ts_total = {}, Decimal("0"), Decimal("0"), Decimal("0")
    for g, a in sorted(acc.items()):
        r = float(rates.get(g) or 0) if rates else 0.0
        ts = float(a["ts"])
        if mode == "ttc":
            base = float(a["amount"]) + round2(ts * (1 + r / 100.0))
        else:
            base = float(a["amount"]) + ts
        t, ht, vat = group_amounts(base, r, mode, g)
        groups[g] = {"total": t, "ht": ht, "vat": vat, "ts": round2(ts), "rate": r}
        total += _d(t)
        vtotal += _d(vat)
        ts_total += _d(ts)
    return {"groups": groups, "total": round2(total), "vtotal": round2(vtotal), "ts": round2(ts_total)}


GROUP_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def response_groups(resp):
    """Per-group figures from an e-MCF InvoiceResponseDataDto (ta..tz, taa..taz, haa..haz,
    vaa..vaz). Groups with no amount are skipped."""
    out = {}
    if not isinstance(resp, dict):
        return out
    for letter in GROUP_LETTERS.lower():
        total = resp.get("ta" + letter)
        ht = resp.get("ha" + letter)
        vat = resp.get("va" + letter)
        if not any(float(x or 0) for x in (total, ht, vat)):
            continue
        out[letter.upper()] = {
            "rate": float(resp.get("t" + letter) or 0),
            "total": round2(total), "ht": round2(ht), "vat": round2(vat),
        }
    return out


def response_rates(resp):
    rates = {}
    if isinstance(resp, dict):
        for letter in GROUP_LETTERS.lower():
            if resp.get("t" + letter) is not None:
                try:
                    rates[letter.upper()] = float(resp.get("t" + letter))
                except (TypeError, ValueError):
                    pass
    return rates


def compare(expected, actual, tolerance):
    """List of human-readable differences between two compute_totals-like dicts."""
    tol = float(tolerance or 0)
    diffs = []
    for key, label in (("total", "Total TTC"), ("vtotal", "Total TVA")):
        e, a = float(expected.get(key) or 0), float(actual.get(key) or 0)
        if abs(e - a) > tol:
            diffs.append(f"{label}: SFE={e:.2f} / e-MCF={a:.2f}")
    eg, ag = expected.get("groups") or {}, actual.get("groups") or {}
    for g in sorted(set(eg) | set(ag)):
        e, a = eg.get(g) or {}, ag.get(g) or {}
        for key, label in (("total", "total"), ("vat", "TVA")):
            ev, av = float(e.get(key) or 0), float(a.get(key) or 0)
            if abs(ev - av) > tol:
                diffs.append(f"Groupe {g} {label}: SFE={ev:.2f} / e-MCF={av:.2f}")
    return diffs
