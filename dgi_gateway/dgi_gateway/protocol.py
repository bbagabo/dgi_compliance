"""MCF-SFE protocol v1.0 (DGI RDC): framing, escaping, command data and response parsing.

Pure functions only (no I/O) so that they can be tested without a device.

Command frame : SOH LEN SEQ CMD DATA AMB BCC(4) ETX
Response frame: SOH LEN SEQ CMD DATA BRK STA(6) AMB BCC(4) ETX
  LEN = number of bytes from LEN to AMB (included) + 20h  (one byte, kept modulo 256: long
        responses such as 33h can exceed 223 bytes, observed on the ELTRADE CC300)
  BCC = 16-bit sum of the bytes from LEN to AMB, sent as 4 ASCII nibbles + 30h
        (12ABh -> 31h 32h 3Ah 3Bh)
One-byte answers: NAK (15h) = resend the same frame with the SAME SEQ; SYN (16h) = wait.
"""
from dataclasses import dataclass
import re

SOH, ETX, BRK, AMB = 0x01, 0x03, 0x04, 0x05
NAK, SYN = 0x15, 0x16

CMD_STATUS = 0xC1        # etat du MCF
CMD_SERVER = 0xC2        # etat de la connexion au serveur DGI / RESTART
CMD_TAXPAYER = 0x2B      # info contribuable
CMD_CLIENT = 0xC3        # informations du client (avant C0h)
CMD_OPEN = 0xC0          # debut de facture
CMD_ITEM = 0x31          # article
CMD_SUBTOTAL = 0x33      # sous-total
CMD_PAYMENT = 0x35       # paiement
CMD_INFO = 0x36          # lignes supplementaires A-H
CMD_FINALIZE = 0x38      # normalisation / annulation (C)

COMMAND_NAMES = {
    CMD_STATUS: "C1h etat", CMD_SERVER: "C2h connexion DGI", CMD_TAXPAYER: "2Bh contribuable",
    CMD_CLIENT: "C3h client", CMD_OPEN: "C0h ouverture", CMD_ITEM: "31h article",
    CMD_SUBTOTAL: "33h sous-total", CMD_PAYMENT: "35h paiement", CMD_INFO: "36h ligne sup.",
    CMD_FINALIZE: "38h normalisation",
}

MAX_DATA = 200
STA_LEN = 6
SEQ_MIN, SEQ_MAX = 0x20, 0xFF
# FACNO v2.0: groups A-Z. Protocol v1.0 documents A-P; an updated MCF reports more groups in C1h
# (H6, confirmed by the DGI): the number of groups is read from the device, never assumed.
TAX_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
PROTOCOL_V1_GROUPS = 16
SALE_TYPES = ("FV", "FT", "EV", "ET")
CREDIT_TYPES = ("FA", "EA")
CREDIT_NATURES = ("COR", "RAN", "RAM", "RRR")
CLIENT_TYPES = ("PP", "PM", "PC", "PL", "AO")
ITEM_TYPES = ("BIE", "SER", "TAX")
# 35h letters of protocol v1.0; credit sales (H5) use the letter configured in ERPNext for the
# updated MCF, so any single letter A-Z sent by the server is accepted.
PAYMENT_LETTERS = ("E", "V", "C", "M", "D", "A")
COMMENT_IDS = "ABCDEFGH"
SIG_RE = re.compile(r"^[A-Z0-9]{24}$")


class ProtocolError(Exception):
    """A frame or a response does not follow the MCF-SFE specification."""


# --------------------------------------------------------------------------- escaping (4.2)
_ESCAPES = (("&", "^amp;"), ("\r", "^xa;"), ("\n", "^xd;"), (",", "^x2c;"), ("<", "^lt;"), (">", "^gt;"))
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def escape_text(value) -> str:
    """Escape a text field (section 4.2). Tabs become spaces (a TAB is a 31h separator)."""
    s = "" if value is None else str(value)
    s = s.replace("\t", " ")
    s = _CONTROL_RE.sub("", s)
    for raw, rep in _ESCAPES:
        s = s.replace(raw, rep)
    return s


def unescape_text(value: str) -> str:
    s = value or ""
    for raw, rep in reversed(_ESCAPES):
        s = s.replace(rep, raw)
    return s


_TOKEN_RE = re.compile(r"\^[a-z0-9]+;|.", re.S)


def truncate_utf8(escaped: str, max_bytes: int) -> str:
    """Cut an ALREADY escaped text at max_bytes UTF-8 bytes without breaking a ^...; sequence."""
    out, size = [], 0
    for tok in _TOKEN_RE.findall(escaped or ""):
        n = len(tok.encode("utf-8"))
        if size + n > max_bytes:
            break
        out.append(tok)
        size += n
    return "".join(out)


def field(value, max_bytes: int | None = None) -> str:
    s = escape_text(value)
    return truncate_utf8(s, max_bytes) if max_bytes else s


# --------------------------------------------------------------------------- numbers
def fmt_amount(value) -> str:
    return "%.2f" % (round(float(value or 0), 2) + 0.0)       # + 0.0 after rounding: never "-0.00"


def fmt_rate(value) -> str:
    return "%.2f" % float(value or 0)


def fmt_qty(value) -> str:
    s = "%.3f" % float(value if value not in (None, "") else 1)
    return s.rstrip("0").rstrip(".")


def to_float(value, default=0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- framing
def bcc16(core: bytes) -> bytes:
    total = sum(core) & 0xFFFF
    return bytes(0x30 + ((total >> shift) & 0xF) for shift in (12, 8, 4, 0))


def next_seq(seq: int) -> int:
    return SEQ_MIN if seq >= SEQ_MAX or seq < SEQ_MIN else seq + 1


def build_frame(seq: int, cmd: int, data: bytes = b"") -> bytes:
    if not (SEQ_MIN <= seq <= SEQ_MAX):
        raise ProtocolError(f"SEQ hors plage 20h-FFh : {seq:#x}")
    if not (0 <= cmd <= 0xFF):
        raise ProtocolError("code de commande hors plage")
    if len(data) > MAX_DATA:
        raise ProtocolError(f"donnees de {len(data)} octets : le MCF en accepte {MAX_DATA} au maximum")
    core = bytes([0x20 + 4 + len(data), seq, cmd]) + data + bytes([AMB])
    return bytes([SOH]) + core + bcc16(core) + bytes([ETX])


@dataclass
class Response:
    seq: int
    cmd: int
    data: bytes
    sta: bytes

    @property
    def text(self) -> str:
        return self.data.decode("utf-8", "replace")


def parse_command_frame(frame: bytes) -> tuple[int, int, bytes]:
    """(seq, cmd, data) of a command frame - used by the simulator."""
    if len(frame) < 10 or frame[0] != SOH or frame[-1] != ETX:
        raise ProtocolError("trame de commande invalide")
    core, rx = frame[1:-5], frame[-5:-1]
    if bcc16(core) != rx:
        raise ProtocolError("BCC de commande invalide")
    if core[-1] != AMB:
        raise ProtocolError("delimiteur AMB absent")
    return core[1], core[2], bytes(core[3:-1])


def parse_response_frame(frame: bytes) -> Response:
    """Complete response frame (SOH..ETX) -> Response. LEN is checked modulo 256."""
    if len(frame) < 1 + 3 + 1 + STA_LEN + 1 + 4 + 1:
        raise ProtocolError(f"trame de reponse trop courte ({len(frame)} octets)")
    if frame[0] != SOH or frame[-1] != ETX:
        raise ProtocolError("SOH/ETX absents de la trame de reponse")
    core, rx = frame[1:-5], frame[-5:-1]
    if bcc16(core) != rx:
        raise ProtocolError(f"BCC invalide (recu {rx.hex()}, attendu {bcc16(core).hex()})")
    if (len(core) + 0x20) % 256 != core[0]:
        raise ProtocolError("la longueur de la trame ne correspond pas au champ LEN")
    if core[-1] != AMB:
        raise ProtocolError("delimiteur AMB absent")
    brk = len(core) - 1 - STA_LEN - 1
    if brk < 3 or core[brk] != BRK:
        raise ProtocolError("delimiteur BRK (04h) absent")
    return Response(seq=core[1], cmd=core[2], data=bytes(core[3:brk]), sta=bytes(core[brk + 1:brk + 1 + STA_LEN]))


# --------------------------------------------------------------------------- status bytes (annexe)
STATUS_BITS = {
    (0, 2): "date_heure_non_reglee", (0, 1): "commande_non_valide", (0, 0): "erreur_syntaxe",
    (1, 4): "rapport_quotidien_requis", (1, 2): "memoire_reinitialisee",
    (1, 1): "commande_interdite_mode_actuel", (1, 0): "depassement_sommes_rapport",
    (2, 3): "facture_ouverte",
    (4, 4): "memoire_pleine", (4, 3): "memoire_moins_50", (4, 2): "enregistrement_invalide",
    (4, 0): "erreur_ecriture_memoire",
}
BLOCKING_FLAGS = ("memoire_pleine", "enregistrement_invalide", "erreur_ecriture_memoire")


def status_flags(sta: bytes) -> list[str]:
    out = []
    for (byte_i, bit), name in STATUS_BITS.items():
        if byte_i < len(sta) and (sta[byte_i] >> bit) & 1:
            out.append(name)
    return out


def invoice_open(sta: bytes) -> bool:
    return len(sta) > 2 and bool((sta[2] >> 3) & 1)


# --------------------------------------------------------------------------- replies
def classify(text: str) -> tuple[str, str]:
    """('R'|'E'|'P'|'DATA', payload). 'R' success, 'E' error, 'P' wait (38h)."""
    t = (text or "").strip()
    up = t.upper()
    for kind in ("E", "P", "R"):
        if up.startswith(kind + ":"):
            return kind, t[2:].strip()
    if up == "R" or re.match(r"^R\s*-?\d", up):
        return "R", t[1:].strip()
    if up.startswith("E") and len(up) <= 2:
        return "E", ""
    return "DATA", t


def split_fields(text: str) -> list[str]:
    return [unescape_text(x).strip() for x in (text or "").split(",")]


def nid_of(mid: str) -> str:
    """MID = NID-ACNT -> NID."""
    mid = (mid or "").strip()
    return mid.rsplit("-", 1)[0] if "-" in mid else mid


def parse_status(text: str) -> dict:
    """C1h: MID,NIF,DT,TC,FVC,FRC,DFDT,DFT,DFS,DFN,DMV,TAXA..TAXP[,TAXQ..] (as many rates as groups)"""
    f = split_fields(text)
    keys = ("mid", "nif", "dt", "tc", "fvc", "frc", "dfdt", "dft", "dfs", "dfn", "dmv")
    out = {k: (f[i] if i < len(f) else "") for i, k in enumerate(keys)}
    out["nid"] = nid_of(out["mid"])
    out["complete"] = len(f) >= len(keys)
    out["tax_rates"] = {}
    rates = f[len(keys):]
    for j, value in enumerate(rates[:len(TAX_LETTERS)]):
        if value != "":
            out["tax_rates"][TAX_LETTERS[j]] = to_float(value)
    out["group_count"] = min(len(rates), len(TAX_LETTERS))
    return out


def parse_server_status(text: str) -> dict:
    """C2h: EC,DC,DT,STA[,ER]"""
    f = split_fields(text)
    return {"sent": f[0] if len(f) > 0 else "", "pending": f[1] if len(f) > 1 else "",
            "last_sync": f[2] if len(f) > 2 else "", "state": f[3] if len(f) > 3 else "",
            "error": f[4] if len(f) > 4 else ""}


def parse_open_invoice(text: str) -> dict:
    """C0h success: FC,TC,CNIF,CNOM,CADD,CTEL,CEML,CRCCM"""
    f = split_fields(text)
    return {"fc": f[0] if f else "", "tc": f[1] if len(f) > 1 else ""}


def subtotal_layout(n: int, group_count: int | None = None) -> tuple[str, int, int]:
    """(layout, base index, number of groups) of a 33h answer of n fields.
    - specification: MV, MV(A..), MT(A..), MTS, MCUR      -> n = 2G + 3 (G=16: 35 fields)
    - ELTRADE CC300: MV, MT, MV(A..), MT(A..), MTS, MCUR  -> n = 2G + 4 (G=16: 36 fields)
    The number of groups G comes from C1h when known (updated MCF, FACNO v2.0), else from the parity."""
    candidates = []
    for layout, base, extra in (("spec", 1, 3), ("eltrade", 2, 4)):
        if (n - extra) % 2 == 0:
            g = (n - extra) // 2
            if PROTOCOL_V1_GROUPS <= g <= len(TAX_LETTERS):
                candidates.append((layout, base, g))
    if group_count:
        for c in candidates:
            if c[2] == group_count:
                return c
    if not candidates:
        raise ProtocolError(f"sous-total 33h illisible ({n} champs)")
    return candidates[0]


def parse_subtotal(text: str, group_count: int | None = None) -> dict:
    """33h, any number of tax groups (see subtotal_layout)."""
    f = [to_float(x) for x in split_fields(text)]
    layout, base, g = subtotal_layout(len(f), group_count)
    mv, mt_total = f[0], (f[1] if layout == "eltrade" else None)
    groups = {}
    for i in range(g):
        total, vat = f[base + i], f[base + g + i]
        if total or vat:
            groups[TAX_LETTERS[i]] = {"total": round(total, 2), "vat": round(vat, 2)}
    mt_sum = round(sum(x["vat"] for x in groups.values()), 2)
    return {"mv": round(mv, 2), "mt": round(mt_total if mt_total is not None else mt_sum, 2),
            "mts": round(f[base + 2 * g], 2), "mcur": round(f[base + 2 * g + 1], 2),
            "groups": groups, "layout": f"{layout}{len(f)}", "group_count": g}


def parse_payment(text: str) -> float:
    """35h success: R{MP} (MP < 0: amount still due, > 0: change)."""
    kind, payload = classify(text)
    if kind != "R":
        raise ProtocolError(f"reponse 35h inattendue : {text!r}")
    return to_float(payload.lstrip(":"), 0.0)


def parse_normalized(payload: str, expected_fn: str | None = None) -> dict:
    """38h success R:FC,TC,FT,DT,MID,NIF,FN,SIG. The specification example swaps FN and SIG:
    resolved with the expected invoice number, then with the 24-character SIG format."""
    f = split_fields(payload)
    if len(f) < 8:
        raise ProtocolError(f"reponse 38h incomplete : {payload!r}")
    a, b = f[6], f[7]
    if expected_fn and a == expected_fn:
        fn, sig = a, b
    elif expected_fn and b == expected_fn:
        fn, sig = b, a
    elif SIG_RE.match(b.upper()) and not SIG_RE.match(a.upper()):
        fn, sig = a, b
    elif SIG_RE.match(a.upper()) and not SIG_RE.match(b.upper()):
        fn, sig = b, a
    else:
        fn, sig = a, b
    return {"fc": f[0], "tc": f[1], "ft": f[2], "dt": f[3], "mid": f[4], "nif": f[5],
            "fn": fn, "sig": sig.upper()}


# --------------------------------------------------------------------------- command data
def client_data(client: dict | None) -> str:
    c = client or {}
    ctype = (c.get("ctype") or "PP").upper()
    if ctype not in CLIENT_TYPES:
        raise ProtocolError(f"type de client invalide : {ctype}")
    parts = [ctype, field(c.get("cnif"), 20), field(c.get("cnom"), 64), field(c.get("cadd"), 64),
             field(c.get("ctel"), 24), field(c.get("ceml"), 48), field(c.get("crccm"), 32)]
    return ",".join(parts)


def open_invoice_data(inv: dict) -> str:
    op = inv.get("operator") or {}
    parts = [field(op.get("id") or "SFE", 16), field(op.get("name") or "SFE", 32), field(inv["nif"], 20)]
    itype = inv["invoice_type"]
    if itype in CREDIT_TYPES:
        cn = inv.get("credit_note") or {}
        if cn.get("rr") not in CREDIT_NATURES:
            raise ProtocolError("nature de l'avoir obligatoire (COR, RAN, RAM ou RRR)")
        rn = (cn.get("rn") or "").replace("-", "").strip()
        if cn.get("rr") != "RRR" and len(rn) != 24:
            raise ProtocolError("la reference d'un avoir est le Code DEF/DGI de 24 caracteres de la facture d'origine")
        parts += [itype, cn["rr"], field(rn if cn.get("rr") != "RRR" or rn else "RRR", 64)]
    elif itype in SALE_TYPES:
        parts.append(itype)
    else:
        raise ProtocolError(f"type de facture invalide : {itype}")
    parts += [(inv.get("pmode") or "TTC").upper(), field(inv["isf"], 20), field(inv["fn"], 64)]
    fx = inv.get("exchange")
    if fx:
        parts += [fmt_amount(fx["crt"]), str(fx["cdt"])[:8]]
    return ",".join(parts)


def item_data(it: dict) -> str:
    """31h: {NOM}[LF{DESC}]TAB{ITYPE},{TAX}{TR}%{MON}TAB{PR}[*{QT}][;T{TSR},{TS}[,{TSDESC}]][TAB{PRORIG},{PRDESC}]"""
    letter = (it.get("tax_group") or "").upper()
    if letter not in TAX_LETTERS or len(letter) != 1:
        raise ProtocolError(f"groupe de taxation invalide : {letter or '?'} (A a Z)")
    itype = (it.get("itype") or "BIE").upper()
    if itype not in ITEM_TYPES:
        raise ProtocolError(f"type d'article invalide : {itype}")
    head = field(it.get("name") or "ARTICLE", 64)
    if it.get("desc"):
        head += "\n" + field(it["desc"], 24)
    data = "{0}\t{1},{2}{3}%{4}\t{5}".format(head, itype, letter, fmt_rate(it.get("tax_rate")),
                                              fmt_amount(it["amount"]), fmt_amount(it["price"]))
    qty = float(it.get("qty") or 1)
    if abs(qty - 1.0) > 1e-9:
        data += "*" + fmt_qty(qty)
    if it.get("ts_value") not in (None, ""):
        ts = ";T{0},{1}".format(str(it["ts_value"]).strip(), fmt_amount(it.get("ts_total") or 0))
        if it.get("ts_desc"):
            ts += "," + field(it["ts_desc"], 16)
        data += ts
    if it.get("orig_price"):
        data += "\t{0},{1}".format(fmt_amount(it["orig_price"]), field(it.get("price_desc") or "Remise", 24))
    return data


def comment_data(line_id: str, content: str) -> str:
    lid = (line_id or "").upper()
    if lid not in COMMENT_IDS or len(lid) != 1:
        raise ProtocolError(f"ligne supplementaire invalide : {line_id}")
    return f"{lid},{field(content, 255)}"


def payment_data(letter: str | None = None, amount=None) -> str:
    if not letter:
        return ""
    letter = letter.upper()
    if len(letter) != 1 or not ("A" <= letter <= "Z"):
        raise ProtocolError(f"mode de paiement invalide : {letter}")
    return letter + (fmt_amount(amount) if amount is not None else "")


def finalize_data(mv, mt) -> str:
    return f"{fmt_amount(mv)},{fmt_amount(mt)}"


def qr_content(mid: str, sig: str, nif: str, dt: str) -> str:
    """Chapter 5: RDCDEF01;{MID};{SIG};{NIF};{DT}"""
    return f"RDCDEF01;{mid};{sig};{nif};{dt}"


def format_sig(sig: str) -> str:
    """24-character SIG printed in groups of 4 (TEST-FACT-HQBA-X2F3-K22N-MDYR)."""
    s = (sig or "").replace("-", "").upper()
    return "-".join(s[i:i + 4] for i in range(0, len(s), 4)) if len(s) == 24 else s
