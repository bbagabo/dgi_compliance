"""In-memory MCF simulator implementing the MCF-SFE v1.0 protocol (for tests and demos).

It behaves like a Transport: the gateway writes command frames, the simulator executes them
and queues the response bytes. Faults can be injected to reproduce field incidents:

* lose_response_for = {0x38}  -> the command IS executed but its response is lost once;
* nak_next = n                -> the next n frames are answered with NAK (not executed);
* syn_before = n              -> n SYN bytes precede each response;
* unplugged = True            -> every read/write raises LinkError (cable removed);
* pending_38 = n              -> 38h answers P: n times before deciding;
* online = False              -> no DGI connection: credit notes cannot be verified;
* reject_fx = True            -> C0h with an exchange rate answers E: IFU (ELTRADE behaviour);
* subtotal_layout = 'eltrade' | 'spec' (36/35 fields with 16 groups);
* groups = 16 (protocol v1.0, A-P) or 19..26 (updated MCF, FACNO v2.0 groups announced by C1h);
* extra_payment_letters = ('R',) -> 35h letters accepted besides E V C M D A (credit, H5).
"""
import hashlib
from datetime import datetime

from . import protocol as P
from .device import LinkError, Transport


class MCFSimulator(Transport):
    def __init__(self, nid="SIM0000001", nif="A0700190Q", *, acnt=1, rates=None,
                 subtotal_layout="eltrade36", fn_sig_order="sig_fn", name="SIM", groups=16,
                 extra_payment_letters=()):
        self.name = name
        self.nid, self.acnt, self.nif = nid, acnt, nif
        self.letters = P.TAX_LETTERS[:groups]
        self.rates = {letter: 0.0 for letter in self.letters}
        self.rates.update({k: v for k, v in (rates or {"B": 16.0, "C": 5.0, "O": 1.0, "P": 0.0}).items()
                           if k in self.letters})
        self.payment_letters = tuple(P.PAYMENT_LETTERS) + tuple(extra_payment_letters)
        self.subtotal_layout = subtotal_layout
        self.fn_sig_order = fn_sig_order
        self.taxpayer = {"I0": "DGI TEST SARL", "I1": "Boulevard du 30 juin", "I2": "Kinshasa",
                         "I3": "+243815105198", "I4": "facturation@dgitest.cd"}
        # counters and journal
        self.tc = self.fvc = self.frc = 0
        self.sent = 0
        self.last = {"dt": "", "ft": "", "sig": "", "fn": "", "mv": 0.0}
        self.invoices = []           # normalized invoices (what the DGI would receive)
        self.executed = []           # executed command codes (to prove no double execution)
        # current invoice
        self._client = None
        self._inv = None
        # link state
        self._out = bytearray()
        self._last_rx = None         # (seq, cmd) of the last correctly received frame
        self._last_response = b""
        # faults
        self.lose_response_for = set()
        self.nak_next = 0
        self.syn_before = 0
        self.unplugged = False
        self.pending_38 = 0
        self.online = True
        self.reject_fx = False
        self.memory_full = False

    # ------------------------------------------------------------------ transport
    def write(self, data: bytes) -> None:
        if self.unplugged:
            raise LinkError("simulateur debranche")
        seq, cmd, payload = P.parse_command_frame(bytes(data))
        if self.nak_next > 0:
            self.nak_next -= 1
            self._out += bytes([P.NAK])
            return
        if self._last_rx == (seq, cmd):
            response = self._last_response          # repeated frame: answer again, do not execute
        else:
            text = self._execute(cmd, payload.decode("utf-8"))
            response = self._frame(seq, cmd, text)
            self._last_rx, self._last_response = (seq, cmd), response
        if cmd in self.lose_response_for:
            self.lose_response_for.discard(cmd)
            return                                      # executed, but the answer is lost
        self._out += bytes([P.SYN]) * self.syn_before + response

    def read(self, size: int, timeout: float) -> bytes:
        if self.unplugged:
            raise LinkError("simulateur debranche")
        chunk = bytes(self._out[:size])
        del self._out[:size]
        return chunk

    def reset_input(self) -> None:
        self._out.clear()

    # ------------------------------------------------------------------ helpers
    def _sta(self) -> bytes:
        b = [0x80] * P.STA_LEN
        if self._inv is not None:
            b[2] |= 1 << 3
        if self.memory_full:
            b[4] |= 1 << 4
        return bytes(b)

    def _frame(self, seq: int, cmd: int, text: str) -> bytes:
        data = text.encode("utf-8")
        core = bytes([(0x20 + 3 + len(data) + 1 + P.STA_LEN + 1) % 256, seq, cmd]) + data + bytes([P.BRK]) \
            + self._sta() + bytes([P.AMB])
        return bytes([P.SOH]) + core + P.bcc16(core) + bytes([P.ETX])

    @property
    def mid(self) -> str:
        return f"{self.nid}-{self.acnt}"

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%Y%m%d%H%M%S")

    # ------------------------------------------------------------------ commands
    def _execute(self, cmd: int, data: str) -> str:
        self.executed.append(cmd)
        handler = {
            P.CMD_STATUS: self._c1, P.CMD_SERVER: self._c2, P.CMD_TAXPAYER: self._2b,
            P.CMD_CLIENT: self._c3, P.CMD_OPEN: self._c0, P.CMD_ITEM: self._31,
            P.CMD_SUBTOTAL: self._33, P.CMD_PAYMENT: self._35, P.CMD_INFO: self._36,
            P.CMD_FINALIZE: self._38,
        }.get(cmd)
        if not handler:
            return "E:COMMANDE INCONNUE"
        return handler(data)

    def _c1(self, _data):
        rates = ",".join("%.2f" % self.rates[x] for x in self.letters)
        return ",".join([self.mid, self.nif, self._now(), str(self.tc), str(self.fvc), str(self.frc),
                         self.last["dt"], self.last["ft"], self.last["sig"],
                         P.escape_text(self.last["fn"]), "%.2f" % self.last["mv"], rates])

    def _c2(self, data):
        state = "RES" if data == "RESTART" else ("CON" if self.online else "DIS")
        if self.online:
            self.sent = self.tc
        # optional ER (manufacturer's last message): the MCF tested on 04/11/2026 answers e.g. "Operation terminee!"
        msg = getattr(self, "server_message", "")
        return f"{self.sent},{self.tc - self.sent},{self._now()},{state}" + (f",{P.escape_text(msg)}" if msg else "")

    def _2b(self, data):
        return P.escape_text(self.taxpayer.get(data, ""))

    def _c3(self, data):
        f = P.split_fields(data)
        if f[0] not in P.CLIENT_TYPES:
            return "E:TYPE DE CLIENT INVALIDE"
        self._client = f
        return "R"

    def _c0(self, data):
        if self._inv is not None:
            return "E:UNE FACTURE EST DEJA OUVERTE"
        if self._client is None:
            return "E:INFORMATIONS DU CLIENT MANQUANTES (C3H)"
        if self.memory_full:
            return "E:MEMOIRE PLEINE"
        f = P.split_fields(data)
        if f[2] != self.nif:
            return "E:NIF INVALIDE"
        itype = f[3]
        i = 4
        rr = rn = None
        if itype in P.CREDIT_TYPES:
            rr, rn, i = f[4], f[5], 6
        pmode, isf, fn = f[i], f[i + 1], f[i + 2]
        fx = f[i + 3:i + 5] if len(f) > i + 3 else []
        if fx and self.reject_fx:
            return "E: IFU"
        if fn == self.last["fn"]:
            return "E:LE NUMERO DE FACTURE EST DEJA UTILISE POUR LA FACTURE PRECEDENTE"
        counter = (self.frc if itype in P.CREDIT_TYPES else self.fvc) + 1
        self._inv = {"type": itype, "rr": rr, "rn": rn, "pmode": pmode.upper(), "isf": isf, "fn": fn,
                     "groups": {}, "ts": 0.0, "paid": 0.0, "fc": counter, "client": self._client}
        self._client = None
        return f"{counter},{self.tc + 1},,,,,,"

    def _31(self, data):
        if self._inv is None:
            return "E:AUCUNE FACTURE OUVERTE"
        head, rest = data.split("\t", 1)
        spec, tail = (rest.split("\t", 1) + [""])[:2]
        itype, tax = spec.split(",", 1)
        letter = tax[0]
        if letter not in self.letters:
            return "E:GROUPE DE TAXATION INVALIDE"
        rate_s, amount_s = tax[1:].split("%", 1)
        if abs(float(rate_s) - self.rates.get(letter, -1)) > 0.001:
            return "E:TAUX DE TAXATION INVALIDE"
        price_part = tail.split("\t", 1)[0]
        ts_total = 0.0
        if ";T" in price_part:
            price_part, ts = price_part.split(";T", 1)
            ts_total = float(ts.split(",")[1])
        if "*" in price_part:
            price, qty = (float(x) for x in price_part.split("*", 1))
        else:
            price, qty = float(price_part), 1.0
        amount = round(price * qty, 2)
        if abs(amount - float(amount_s)) > 0.011:
            return "E:MONTANT DE LIGNE INCOHERENT"
        g = self._inv["groups"].setdefault(letter, 0.0)
        self._inv["groups"][letter] = round(g + amount, 2)
        self._inv["ts"] = round(self._inv["ts"] + ts_total, 2)
        vat = self._vat(letter, amount)
        mv = amount if self._inv["pmode"] == "TTC" else amount + vat
        return "%.2f,%.2f,%.2f,%.2f" % (mv + ts_total, (amount - vat) if self._inv["pmode"] == "TTC" else amount, vat, ts_total)

    def _vat(self, letter, amount):
        r = self.rates.get(letter, 0.0)
        if not r:
            return 0.0
        if self._inv["pmode"] == "TTC":
            return round(amount * r / (100.0 + r), 2)
        return round(amount * r / 100.0, 2)

    def _totals(self):
        groups = {}
        for letter, mon in self._inv["groups"].items():
            vat = self._vat(letter, mon)
            total = mon if self._inv["pmode"] == "TTC" else round(mon + vat, 2)
            groups[letter] = (round(total, 2), vat)
        mv = round(sum(t for t, _ in groups.values()) + self._inv["ts"], 2)
        mt = round(sum(v for _, v in groups.values()), 2)
        return mv, mt, groups

    def _33(self, _data):
        if self._inv is None:
            return "E:AUCUNE FACTURE OUVERTE"
        mv, mt, groups = self._totals()
        mva = ["%.2f" % groups.get(x, (0.0, 0.0))[0] for x in self.letters]
        mta = ["%.2f" % groups.get(x, (0.0, 0.0))[1] for x in self.letters]
        fields = ["%.2f" % mv] + (["%.2f" % mt] if self.subtotal_layout.startswith("eltrade") else []) \
            + mva + mta + ["%.2f" % self._inv["ts"], "0"]
        return ",".join(fields)

    def _35(self, data):
        if self._inv is None:
            return "E:AUCUNE FACTURE OUVERTE"
        mv, _mt, _g = self._totals()
        if self._inv["paid"] >= mv - 0.001:
            return "E:LE MONTANT EST DEJA PAYE"
        if data:
            letter = data[0]
            if letter not in self.payment_letters:
                return "E:MODE DE PAIEMENT INVALIDE"
            amount = float(data[1:]) if len(data) > 1 else mv - self._inv["paid"]
        else:
            amount = mv - self._inv["paid"]
        self._inv["paid"] = round(self._inv["paid"] + amount, 2)
        return "R%.2f" % round(self._inv["paid"] - mv, 2)

    def _36(self, data):
        if self._inv is None:
            return "E:AUCUNE FACTURE OUVERTE"
        return "R"

    def _38(self, data):
        if self._inv is None:
            return "E:AUCUNE FACTURE OUVERTE"
        if data == "C":
            self._inv = None
            return "E:ANNULEE"
        mv, mt, _g = self._totals()
        try:
            rx_mv, rx_mt = (float(x) for x in data.split(","))
        except ValueError:
            return "E:DONNEES INVALIDES"
        if abs(rx_mv - mv) > 0.001 or abs(rx_mt - mt) > 0.001:
            return "E:LE MONTANT N'EST PAS VALIDE"
        if self._inv["type"] in P.CREDIT_TYPES:
            if self.pending_38 > 0:
                self.pending_38 -= 1
                return f"P:{self.mid},{self.nif},{self._inv['fn']},{mv:.2f},VERIFICATION DE L'AVOIR EN COURS"
            if not self.online:
                return "E:LA FACTURE D'AVOIR N'A PAS PU ETRE VERIFIEE POUR LE MOMENT, VEUILLEZ REESSAYER"
        elif self.pending_38 > 0:
            self.pending_38 -= 1
            return f"P:{self.mid},{self.nif},{self._inv['fn']},{mv:.2f},TRAITEMENT EN COURS"
        inv = self._inv
        self._inv = None
        if inv["paid"] < mv:
            inv["paid"] = mv                             # remainder recorded as cash (35h)
        self.tc += 1
        if inv["type"] in P.CREDIT_TYPES:
            self.frc += 1
        else:
            self.fvc += 1
        dt = self._now()
        sig = hashlib.sha256(f"{self.nid}|{self.tc}|{inv['fn']}|{mv}".encode()).hexdigest()
        sig = "".join(c for c in sig.upper() if c.isalnum())[:24]
        sig = (sig + "X" * 24)[:24]
        self.last = {"dt": dt, "ft": inv["type"], "sig": sig, "fn": inv["fn"], "mv": mv}
        self.invoices.append({"fn": inv["fn"], "type": inv["type"], "sig": sig, "mv": mv, "mt": mt,
                              "tc": self.tc, "fc": inv["fc"]})
        fn = P.escape_text(inv["fn"])
        tail = f"{sig},{fn}" if self.fn_sig_order == "sig_fn" else f"{fn},{sig}"
        return f"R:{inv['fc']},{self.tc},{inv['type']},{dt},{self.mid},{self.nif},{tail}"
