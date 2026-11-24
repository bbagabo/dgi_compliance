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
before CONFIRM (API e-MCF v2.0, workflow p.5) and rebuild a breakdown when no e-MCF
response is stored (invoices normalized before v5.0.0).

v5.5.0 (specifications v2, October 2026): 19 applicable groups A-S, T-Z reserved; rates of
groups Q-Z read from trq..trz (the field 'ts' is the specific tax, not the rate of group S).
"""
from decimal import Decimal, ROUND_HALF_UP, ROUND_CEILING

VAT_ONLY_GROUPS = ("N",)  # SFE 1.5.8
# DGI "Types d'article" table (annex of the SFE specifications, SFE 1.1.16): items of type TAX only in
# groups L (prelevements sur ventes) and N (TVA specifique); BIE and SER items in every group but L and N.
TAX_ITEM_GROUPS = ("L", "N")


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


def item_type_group_error(item_type, group):
    """DGI table "Types d'article": None when the item type may be recorded in the group, else the reason.
    TAX (taxes et redevances) only in L and N; BIE and SER never in L or N."""
    t = str(item_type or "").strip().upper()
    g = str(group or "").strip().upper()
    if not t or not g:
        return None
    if t == "TAX" and g not in TAX_ITEM_GROUPS:
        return ("article de type TAX dans le groupe {0} : le type TAX est reserve aux groupes L (prelevements "
                "sur ventes) et N (TVA specifique)").format(g)
    if t in ("BIE", "SER") and g in TAX_ITEM_GROUPS:
        return ("article de type {0} dans le groupe {1} : les groupes L et N n'acceptent que des articles de "
                "type TAX (taxes et redevances)").format(t, g)
    return None


def mcf_expected_totals(items, mode):
    """Figures the MCF must give at 33h for the items sent with 31h: MV (total with VAT and specific tax),
    MT (VAT total) and TS. items: rows with tax_group, tax_rate, amount (MON) and optional ts_total.
    TTC: VAT extracted from the group amount; HT: VAT added. SFE 1.5.8: the amount of group N is
    entirely VAT, in both modes (v5.5.2; until v5.5.1 group N counted as 0 VAT and the MCF subtotal
    was refused as a difference)."""
    mode = (mode or "TTC").upper()
    groups = {}
    ts = 0.0
    for row in items or []:
        letter = str(row.get("tax_group") or "").upper()
        g = groups.setdefault(letter, {"rate": float(row.get("tax_rate") or 0), "mon": 0.0})
        g["mon"] = round(g["mon"] + float(row.get("amount") or 0), 2)
        ts = round(ts + float(row.get("ts_total") or 0), 2)
    mv = mt = 0.0
    for letter, g in groups.items():
        r = g["rate"]
        if letter in VAT_ONLY_GROUPS:
            vat = g["mon"]
            mv = round(mv + g["mon"], 2)
        elif mode == "TTC":
            vat = round(g["mon"] * r / (100.0 + r), 2) if r else 0.0
            mv = round(mv + g["mon"], 2)
        else:
            vat = round(g["mon"] * r / 100.0, 2) if r else 0.0
            mv = round(mv + g["mon"] + vat, 2)
        mt = round(mt + vat, 2)
    return {"mv": round(mv + ts, 2), "mt": mt, "ts": ts}


def commercial_tax_table(rows, total_vat):
    """Per-group VAT table in the INVOICE currency (foreign-currency invoices).

    rows: one dict per invoice line {group, label, rate, net} (net = ERPNext net amount of the line).
    total_vat: ERPNext tax total, allocated to the VAT groups in proportion to net x rate, so that the
    table adds up to the ERPNext taxes. Group N lines are recorded by ERPNext as items, outside its tax
    total: their whole amount is VAT (SFE 1.5.8), so they are printed as VAT, never as HT (v5.5.2)."""
    groups, net_by_group = {}, {}
    for ln in rows or []:
        g = ln["group"]
        groups.setdefault(g, {"group": g, "label": ln.get("label") or g, "rate": float(ln.get("rate") or 0),
                              "ht": 0.0, "vat": 0.0})
        net_by_group[g] = net_by_group.get(g, 0.0) + abs(float(ln.get("net") or 0))
    keys = sorted(groups)
    shared = [g for g in keys if g not in VAT_ONLY_GROUPS]
    weights = {g: net_by_group.get(g, 0.0) * groups[g]["rate"] for g in shared}
    wsum = sum(weights.values())
    total_vat = abs(float(total_vat or 0))
    allocated = 0.0
    for i, g in enumerate(shared):
        groups[g]["ht"] = round2(net_by_group.get(g, 0.0))
        if wsum:
            vat = round2(total_vat - allocated) if i == len(shared) - 1 else round2(total_vat * weights[g] / wsum)
        else:
            vat = 0.0
        allocated += vat
        groups[g]["vat"] = vat
        groups[g]["total"] = round2(groups[g]["ht"] + vat)
    for g in keys:
        if g in VAT_ONLY_GROUPS:
            amount = round2(net_by_group.get(g, 0.0))
            groups[g].update({"ht": 0.0, "vat": amount, "total": amount})
    return [groups[g] for g in keys]


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


# --------------------------------------------------------------------------- #
# Tax groups - DGI specifications v2 (Note de service n°01/209/DGI/DG/DLCFI/FMM/2026,
# FACNO Groupes de taxation v2.0, protocols MCF-SFE v2 and e-MCF-SFE v2, October 2026)
# --------------------------------------------------------------------------- #
GROUP_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"   # carried by the protocols v2 (26 groups)
HISTORIC_GROUPS = "ABCDEFGHIJKLMNOP"           # protocol v1 / ST1 answers (16 groups)
APPLICABLE_GROUPS = "ABCDEFGHIJKLMNOPQRS"      # 19 groups in force (SFE 1.1.12), homologated scope
RESERVED_GROUPS = "TUVWXYZ"                    # "Reserve 1..7": no item may be recorded in them
HORS_CHAMP_GROUP = "S"                         # v2: hors champ moved from group A to group S


def is_reserved_group(letter) -> bool:
    return bool(letter) and str(letter).upper() in RESERVED_GROUPS


def reserved_label(letter) -> str:
    """'Reserve 1' for T ... 'Reserve 7' for Z (FACNO v2.0)."""
    return "Reserve %d" % (RESERVED_GROUPS.index(str(letter).upper()) + 1)


def rate_field(letter) -> str:
    """Name of the RATE of a group in the e-MCF API v2 responses: ta..tp for A-P, trq..trz for
    Q-Z. 'ts' is the specific tax amount, never the rate of group S."""
    low = str(letter).lower()
    return ("t" + low) if low <= "p" else ("tr" + low)


def _response_rate(resp, letter):
    """Rate of a group in an e-MCF response, or None. API v2 name first; for Q-Z, the v1.x
    continuation (tq, tr, tt..tz) is accepted as a fallback, except 'ts' (specific tax)."""
    low = str(letter).lower()
    keys = [rate_field(low)]
    if low > "p" and low != "s":
        keys.append("t" + low)
    for key in keys:
        if resp.get(key) is not None:
            try:
                return float(resp.get(key))
            except (TypeError, ValueError):
                return None
    return None


def response_groups(resp):
    """Per-group figures from an e-MCF InvoiceResponseDataDto (API v2: rates ta..tp and trq..trz,
    totals taa..taz, HT haa..haz, VAT vaa..vaz). Groups with no amount are skipped."""
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
            "rate": _response_rate(resp, letter) or 0.0,
            "total": round2(total), "ht": round2(ht), "vat": round2(vat),
        }
    return out


def response_rates(resp):
    rates = {}
    if isinstance(resp, dict):
        for letter in GROUP_LETTERS.lower():
            r = _response_rate(resp, letter)
            if r is not None:
                rates[letter.upper()] = r
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
