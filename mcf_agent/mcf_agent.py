#!/usr/bin/env python3
"""Agent MCF local - DGI Compliance v4.0

Pont entre le dispositif fiscal physique MCF (Machine de Controle Fiscal, port COM
virtuel USB) et un site ERPNext/Frappe Cloud executant l'app dgi_compliance en mode
'MCF Physique (USB)'.

  ERPNext (cloud)  <--HTTPS (API token)-->  cet agent  <--serie 115200 8N1-->  MCF

Protocole implemente: "Specifications techniques des dispositifs electroniques
fiscaux - PROTOCOLE MCF-SFE v1.0" (DGI RDC):
  - trame commande : SOH LEN SEQ CMD DATA AMB(05h) BCC(4 x ASCII) ETX
  - trame reponse  : SOH LEN SEQ CMD DATA BRK(04h) STA(6) AMB(05h) BCC ETX
  - LEN = octets de LEN a AMB inclus + 20h ; BCC = somme 16 bits de LEN a AMB,
    transmis en 4 quartets ASCII +30h (12ABh -> 31h 32h 3Ah 3Bh)
  - reponses 1 octet: NAK(15h) = repeter meme SEQ ; SYN(16h) = patienter
  - commandes: C1h etat, C2h connexion serveur, 2Bh contribuable, C3h client,
    C0h debut facture, 31h article, 33h sous-total, 35h paiements,
    36h infos supplementaires, 38h normalisation / annulation ("C")
  - QR (ch.5): RDCDEF01;{MID};{SIG};{NIF};{DT}

Usage:
  python mcf_agent.py --test            # detection du port + C1h/C2h + heartbeat site
  python mcf_agent.py --once            # traite les jobs en attente puis s'arrete
  python mcf_agent.py                   # boucle de service (Ctrl+C pour arreter)
Configuration: mcf_agent_config.json (voir mcf_agent_config.example.json).
"""
import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None

try:
    import requests
except ImportError:
    requests = None

# ---------------------------------------------------------------- constants
SOH, ETX, BRK, AMB = 0x01, 0x03, 0x04, 0x05
NAK, SYN = 0x15, 0x16
CMD_STATUS, CMD_SERVER, CMD_TAXPAYER = 0xC1, 0xC2, 0x2B
CMD_CLIENT, CMD_OPEN = 0xC3, 0xC0
CMD_ITEM, CMD_SUBTOTAL, CMD_PAY, CMD_INFO, CMD_FINALIZE = 0x31, 0x33, 0x35, 0x36, 0x38
BAUDRATE = 115200
MAX_DATA = 200
STA_LEN = 6

# 4.2 caracteres speciaux (ordre important: '&' d'abord a l'echappement inverse pres)
ESCAPES = [("&", "^amp;"), ("\r", "^xa;"), ("\n", "^xd;"), (",", "^x2c;"),
           ("<", "^lt;"), (">", "^gt;")]

log = logging.getLogger("mcf_agent")


class ProtocolError(Exception):
    pass


class DeviceError(Exception):
    """Erreur definitive renvoyee par le MCF (reponse E:...)."""


class DeviceUnavailable(Exception):
    """Le MCF ne repond pas / port introuvable (transitoire: on reessaiera)."""


# ---------------------------------------------------------------- framing
def escape_text(s: str) -> str:
    s = str(s or "")
    for raw, rep in ESCAPES:
        s = s.replace(raw, rep)
    return s


def unescape_text(s: str) -> str:
    for raw, rep in reversed(ESCAPES):
        s = s.replace(rep, raw)
    return s


def bcc16(core: bytes) -> bytes:
    """Somme 16 bits des octets LEN..AMB, transmise en 4 quartets ASCII (+30h)."""
    total = sum(core) & 0xFFFF
    return bytes(0x30 + ((total >> shift) & 0xF) for shift in (12, 8, 4, 0))


def build_frame(seq: int, cmd: int, data: bytes = b"") -> bytes:
    if not (0x20 <= seq <= 0xFF):
        raise ProtocolError(f"SEQ hors plage 20h-FFh: {seq:#x}")
    if len(data) > MAX_DATA:
        raise ProtocolError(f"DATA > {MAX_DATA} octets ({len(data)})")
    length = 0x20 + 4 + len(data)          # LEN..AMB inclus = 4 + len(data)
    core = bytes([length, seq, cmd]) + data + bytes([AMB])
    return bytes([SOH]) + core + bcc16(core) + bytes([ETX])


def parse_response(frame: bytes) -> dict:
    """Trame reponse complete (SOH..ETX) -> {seq, cmd, data(bytes), sta(bytes)}."""
    if len(frame) < 1 + 4 + 1 + STA_LEN + 1 + 4 + 1:
        raise ProtocolError(f"trame trop courte ({len(frame)} octets)")
    if frame[0] != SOH or frame[-1] != ETX:
        raise ProtocolError("SOH/ETX manquants")
    core, rx_bcc = frame[1:-5], frame[-5:-1]
    if bcc16(core) != rx_bcc:
        raise ProtocolError(f"BCC invalide (recu {rx_bcc.hex()}, attendu {bcc16(core).hex()})")
    length, seq, cmd = core[0], core[1], core[2]
    data_len = (length - 0x20) - (3 + 1 + STA_LEN + 1)   # LEN SEQ CMD | BRK STA AMB
    if data_len < 0 or 3 + data_len + 1 + STA_LEN + 1 != len(core):
        raise ProtocolError(f"LEN incoherent ({length:#x} pour {len(core)} octets)")
    data = core[3:3 + data_len]
    if core[3 + data_len] != BRK:
        raise ProtocolError("delimiteur BRK (04h) absent")
    sta = core[3 + data_len + 1:3 + data_len + 1 + STA_LEN]
    return {"seq": seq, "cmd": cmd, "data": data, "sta": sta}


# Annexe - octets de statut (bits documentes uniquement)
STA_BITS = {
    (0, 2): "date/heure non reglee", (0, 1): "commande non valide",
    (0, 0): "erreur syntaxique dans la commande",
    (1, 4): "rapport quotidien requis", (1, 2): "memoire reinitialisee detectee",
    (1, 1): "commande interdite dans le mode actuel",
    (1, 0): "depassement des sommes du dernier rapport quotidien",
    (2, 3): "facture ouverte",
    (4, 4): "memoire pleine", (4, 3): "memoire < 50 enregistrements",
    (4, 2): "enregistrement invalide en memoire", (4, 0): "erreur d'enregistrement memoire",
}


def sta_flags(sta: bytes) -> list:
    out = []
    for (byte_i, bit), label in STA_BITS.items():
        if byte_i < len(sta) and (sta[byte_i] >> bit) & 1:
            out.append(label)
    return out


def invoice_is_open(sta: bytes) -> bool:
    return len(sta) > 2 and bool((sta[2] >> 3) & 1)


def fmt_amount(v) -> str:
    return "%.2f" % float(v or 0)


def fmt_qty(v) -> str:
    s = "%.3f" % float(v or 1)
    return s.rstrip("0").rstrip(".")


# ---------------------------------------------------------------- device
class MCFDevice:
    """Un MCF sur un port serie ouvert. Gere SEQ, NAK (retransmission), SYN (attente)."""

    def __init__(self, port: str, timeout: float = 0.5):
        if serial is None:
            raise DeviceUnavailable("pyserial n'est pas installe (pip install pyserial)")
        try:
            self.ser = serial.Serial(port, BAUDRATE, bytesize=8, parity="N",
                                     stopbits=1, timeout=timeout)
        except Exception as e:
            raise DeviceUnavailable(f"ouverture du port {port} impossible: {e}")
        self.port = port
        self._seq = 0x20

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    def _next_seq(self) -> int:
        self._seq = 0x20 if self._seq >= 0xFF else self._seq + 1
        return self._seq

    def _read_reply(self, deadline: float):
        """Lit jusqu'a une trame complete, un NAK, ou l'echeance. SYN prolonge l'attente."""
        buf = bytearray()
        while time.monotonic() < deadline:
            chunk = self.ser.read(1)
            if not chunk:
                continue
            b = chunk[0]
            if not buf:
                if b == NAK:
                    return "NAK"
                if b == SYN:                      # le MCF demande plus de temps
                    deadline = max(deadline, time.monotonic() + 2.0)
                    continue
                if b != SOH:
                    continue                      # bruit avant le debut de trame
            buf.append(b)
            if b == ETX and len(buf) > 12:
                return bytes(buf)
        return None

    def transact(self, cmd: int, data: str = "", *, timeout: float = 3.0,
                 retries: int = 3) -> tuple:
        """Envoie une commande, retourne (data_str, sta). NAK => retransmission meme SEQ."""
        payload = data.encode("utf-8") if isinstance(data, str) else (data or b"")
        seq = self._next_seq()
        frame = build_frame(seq, cmd, payload)
        for attempt in range(1, retries + 1):
            self.ser.reset_input_buffer()
            self.ser.write(frame)
            self.ser.flush()
            reply = self._read_reply(time.monotonic() + timeout)
            if reply == "NAK":
                log.warning("NAK recu (cmd %02Xh, tentative %d) - retransmission", cmd, attempt)
                continue
            if reply is None:
                log.warning("pas de reponse (cmd %02Xh, tentative %d)", cmd, attempt)
                continue
            parsed = parse_response(reply)
            if parsed["seq"] != seq:
                log.warning("SEQ inattendu (%02Xh != %02Xh) - ignore", parsed["seq"], seq)
                continue
            flags = sta_flags(parsed["sta"])
            if flags:
                log.debug("STA: %s", "; ".join(flags))
            return parsed["data"].decode("utf-8", "replace"), parsed["sta"]
        raise DeviceUnavailable(f"le MCF ne repond pas a la commande {cmd:02X}h sur {self.port}")

    # ---- commandes ----
    def status(self) -> dict:
        data, sta = self.transact(CMD_STATUS)
        f = [unescape_text(x) for x in data.split(",")]
        keys = ["MID", "NIF", "DT", "TC", "FVC", "FRC", "DFDT", "DFT", "DFS", "DFN", "DMV"]
        out = {k: (f[i] if i < len(f) else "") for i, k in enumerate(keys)}
        for j, letter in enumerate("ABCDEFGHIJKLMNOP"):
            idx = len(keys) + j
            out["TAX" + letter] = f[idx] if idx < len(f) else ""
        out["sta"], out["flags"] = sta, sta_flags(sta)
        return out

    def server_status(self) -> dict:
        data, _sta = self.transact(CMD_SERVER)
        f = [unescape_text(x) for x in data.split(",")]
        return {"EC": f[0] if len(f) > 0 else "", "DC": f[1] if len(f) > 1 else "",
                "DT": f[2] if len(f) > 2 else "", "STA": f[3] if len(f) > 3 else "",
                "ER": f[4] if len(f) > 4 else ""}

    def set_client(self, c: dict):
        data = ",".join(escape_text(c.get(k, "")) for k in
                        ("ctype", "cnif", "cnom", "cadd", "ctel", "ceml", "crccm"))
        resp, _ = self.transact(CMD_CLIENT, data)
        if resp.strip().upper().startswith("E"):
            raise DeviceError(f"C3h (client): {resp}")

    def open_invoice(self, p: dict):
        parts = [escape_text(p["operator"]["id"]), escape_text(p["operator"]["name"]),
                 escape_text(p["nif"])]
        if p.get("credit_note"):
            parts += [p["invoice_type"], p["credit_note"]["rr"],
                      escape_text(p["credit_note"]["rn"])]
        else:
            parts.append(p["invoice_type"])
        parts += [p.get("pmode") or "TTC", escape_text(p["isf"]), escape_text(p["fn"])]
        if p.get("exchange"):
            parts += [fmt_amount(p["exchange"]["crt"]), p["exchange"]["cdt"]]
        resp, _ = self.transact(CMD_OPEN, ",".join(parts), timeout=6.0)
        if resp.strip().upper().startswith("E:"):
            raise DeviceError(f"C0h (debut facture): {resp}")
        return resp

    def add_item(self, it: dict):
        name = escape_text(it["name"]).replace("\t", " ")
        head = name + (("\n" + escape_text(it["desc"])[:24]) if it.get("desc") else "")
        data = "{0}\t{1},{2}{3}%{4}\t{5}".format(
            head, it.get("itype") or "BIE", it["tax_group"],
            fmt_amount(it.get("tax_rate")), fmt_amount(it["amount"]), fmt_amount(it["price"]))
        if float(it.get("qty") or 1) != 1:
            data += "*" + fmt_qty(it["qty"])
        if it.get("orig_price"):
            data += "\t{0},{1}".format(fmt_amount(it["orig_price"]),
                                       escape_text(it.get("price_desc") or "Remise")[:24])
        resp, _ = self.transact(CMD_ITEM, data, timeout=5.0)
        if resp.strip().upper().startswith("E:"):
            raise DeviceError(f"31h (article '{it['name'][:24]}'): {resp}")
        return resp

    def set_comment(self, lid: str, content: str):
        resp, _ = self.transact(CMD_INFO, f"{lid},{escape_text(content)[:255]}")
        if resp.strip().upper().startswith("E"):
            raise DeviceError(f"36h (commentaire {lid}): {resp}")

    def subtotal(self) -> dict:
        data, _ = self.transact(CMD_SUBTOTAL, timeout=5.0)
        f = data.split(",")
        mv = float(f[0] or 0)
        mt = round(sum(float(x or 0) for x in f[17:33]), 2)   # MTA..MTP
        mts = float(f[33] or 0) if len(f) > 33 else 0.0
        mcur = float(f[34] or 0) if len(f) > 34 else 0.0
        return {"mv": mv, "mt": mt, "mts": mts, "mcur": mcur, "raw": data}

    def pay(self, letter: str = None, amount=None):
        data = "" if not letter else (letter + (fmt_amount(amount) if amount is not None else ""))
        resp, _ = self.transact(CMD_PAY, data, timeout=5.0)
        r = resp.strip()
        if r.upper().startswith("E"):
            raise DeviceError(f"35h (paiement {data or 'auto'}): {resp}")
        return r[1:] if r[:1].upper() == "R" else r   # reste/excedent

    def finalize(self, mv, mt, *, expected_fn: str = None,
                 pending_wait: float = 120.0) -> dict:
        """38h - normalisation. Reponse P:... => renvoyer la meme commande (protocole).

        NOTE ordre FN/SIG: la table du protocole donne ...,{FN},{SIG} mais l'exemple
        officiel montre ...,{SIG},{FN}. On resout avec expected_fn (numero SFE connu)."""
        data = f"{fmt_amount(mv)},{fmt_amount(mt)}"
        deadline = time.monotonic() + pending_wait
        while True:
            resp, _ = self.transact(CMD_FINALIZE, data, timeout=10.0)
            r = resp.strip()
            up = r.upper()
            if up.startswith("E:"):
                raise DeviceError(f"38h (normalisation): {r}")
            if up.startswith("P:"):
                if time.monotonic() > deadline:
                    raise DeviceUnavailable(f"38h toujours en attente apres {pending_wait}s: {r}")
                log.info("38h en attente (%s) - nouvel essai...", r[:60])
                time.sleep(1.5)
                continue
            if up.startswith("R"):
                r = r.split(":", 1)[1] if ":" in r[:3] else r[1:]
            f = [unescape_text(x).strip() for x in r.strip().lstrip(":").split(",")]
            if len(f) < 8:
                raise ProtocolError(f"reponse 38h incomplete: {resp}")
            fn, sig = f[6], f[7]                      # ordre de la table ({FN},{SIG})
            if expected_fn and sig == expected_fn:
                fn, sig = f[7], f[6]                  # ordre de l'exemple ({SIG},{FN})
            elif expected_fn and fn != expected_fn and len(f[6]) > len(f[7]):
                fn, sig = f[7], f[6]                  # heuristique: le SIG est le plus long
            return {"fc": f[0], "tc": f[1], "ft": f[2], "dt": f[3],
                    "mid": f[4], "nif": f[5], "fn": fn, "sig": sig}

    def cancel_invoice(self):
        """38h avec 'C' - annule la facture en cours dans le MCF (reponse E:ANNULEE attendue)."""
        try:
            resp, _ = self.transact(CMD_FINALIZE, "C", timeout=6.0)
            log.info("annulation facture MCF: %s", resp.strip()[:60])
        except DeviceError as e:
            log.info("annulation facture MCF: %s", e)


# ---------------------------------------------------------------- detection
def detect_port(explicit: str = None, hint: str = None) -> str:
    """Detection automatique du port USB du MCF: on sonde chaque port serie candidat
    avec C1h; le premier qui repond par une trame valide est retenu."""
    if explicit:
        return explicit
    if list_ports is None:
        raise DeviceUnavailable("pyserial n'est pas installe (pip install pyserial)")
    candidates = [p.device for p in list_ports.comports()]
    if hint and hint in candidates:
        candidates.remove(hint)
        candidates.insert(0, hint)
    if not candidates:
        raise DeviceUnavailable("aucun port serie detecte (MCF branche? pilote USB installe?)")
    log.info("sondage des ports: %s", ", ".join(candidates))
    for port in candidates:
        dev = None
        try:
            dev = MCFDevice(port, timeout=0.3)
            dev.transact(CMD_STATUS, timeout=1.2, retries=1)
            dev.close()
            log.info("MCF detecte sur %s", port)
            return port
        except Exception:
            if dev:
                dev.close()
    raise DeviceUnavailable("MCF introuvable sur les ports: " + ", ".join(candidates))


# ---------------------------------------------------------------- job flow
def run_job(dev: MCFDevice, payload: dict, tolerance: float = 1.0) -> dict:
    """Deroule le flux 4.3 du protocole pour une facture et retourne les elements
    de securite (reponse 38h) + les totaux enregistres par la machine."""
    st = dev.status()
    if invoice_is_open(st["sta"]):
        log.warning("facture restee ouverte dans le MCF - annulation de securite")
        dev.cancel_invoice()

    # Garde fiscale: ne jamais normaliser sur la machine d'un autre contribuable.
    if payload.get("nif") and st.get("NIF") and payload["nif"] != st["NIF"]:
        raise DeviceError(f"NIF machine ({st['NIF']}) different du NIF facture ({payload['nif']})")

    # Coherence des taux par groupe (les taux sont configures dans la machine).
    for it in payload["items"]:
        machine_rate = st.get("TAX" + it["tax_group"], "")
        try:
            if machine_rate != "" and abs(float(machine_rate) - float(it["tax_rate"])) > 0.001:
                raise DeviceError(
                    f"groupe {it['tax_group']}: taux machine {machine_rate}% != "
                    f"taux configure {it['tax_rate']}% (DGI Tax Group)")
        except ValueError:
            pass

    dev.set_client(payload.get("client") or {"ctype": "PP"})
    dev.open_invoice(payload)
    try:
        for it in payload["items"]:
            dev.add_item(it)
        for lid, content in sorted((payload.get("comments") or {}).items()):
            dev.set_comment(lid, content)

        sub = dev.subtotal()
        exp = payload.get("totals") or {}
        if abs(sub["mv"] - float(exp.get("mv") or 0)) > tolerance or \
           abs(sub["mt"] - float(exp.get("mt") or 0)) > tolerance:
            raise DeviceError(
                "ecart de totaux (33h): machine MV={0} MT={1} vs payload MV={2} MT={3} "
                "(tolerance {4} CDF)".format(sub["mv"], sub["mt"],
                                             exp.get("mv"), exp.get("mt"), tolerance))

        payments = payload.get("payments") or []
        if payments:
            for pay in payments:
                rest = dev.pay(pay["letter"], pay["amount"])
                log.debug("35h %s%s -> reste %s", pay["letter"], pay["amount"], rest)
        else:
            dev.pay()   # especes automatique = montant total

        result = dev.finalize(sub["mv"], sub["mt"], expected_fn=payload.get("fn"))
        result["machine_mv"], result["machine_mt"] = sub["mv"], sub["mt"]
        return result
    except DeviceError:
        # Erreur definitive en cours de facture: liberer la machine avant de remonter.
        dev.cancel_invoice()
        raise


# ---------------------------------------------------------------- ERP client
class ERPClient:
    def __init__(self, cfg: dict):
        if requests is None:
            raise SystemExit("le module 'requests' n'est pas installe (pip install requests)")
        self.base = cfg["site_url"].rstrip("/")
        self.headers = {
            "Authorization": "token {0}:{1}".format(cfg["api_key"], cfg["api_secret"]),
            "Content-Type": "application/json", "Accept": "application/json",
        }
        self.agent_id = cfg.get("agent_id") or os.environ.get("COMPUTERNAME", "mcf-agent")

    def _call(self, method: str, **params):
        url = f"{self.base}/api/method/dgi_compliance.mcf.api.{method}"
        r = requests.post(url, headers=self.headers, json=params, timeout=30)
        if r.status_code >= 400:
            raise RuntimeError(f"{method}: HTTP {r.status_code} - {r.text[:300]}")
        return (r.json() or {}).get("message") or {}

    def heartbeat(self, status: dict):
        return self._call("heartbeat", agent_id=self.agent_id, status=status)

    def pull_jobs(self, limit: int = 1):
        return self._call("pull_jobs", agent_id=self.agent_id, limit=limit)

    def push_result(self, job: str, ok: bool, result: dict = None, error: str = None):
        return self._call("push_result", job=job, ok=1 if ok else 0,
                          result=result or {}, error=error)


# ---------------------------------------------------------------- agent loop
def device_snapshot(dev: MCFDevice) -> dict:
    st = dev.status()
    srv = dev.server_status()
    snap = {"port": dev.port, "mid": st.get("MID"), "nif": st.get("NIF"),
            "dt": st.get("DT"), "tc": st.get("TC"), "flags": st.get("flags"),
            "server": {"sta": srv.get("STA"), "last_ok": srv.get("DT"),
                       "sent": srv.get("EC"), "pending": srv.get("DC")}}
    # §C2: alerter si la derniere connexion DGI reussie date de plus d'un jour.
    try:
        last = datetime.strptime(srv.get("DT") or "", "%Y%m%d%H%M%S")
        if (datetime.now() - last).total_seconds() > 86400:
            snap["alert"] = "derniere connexion DGI reussie > 1 jour"
    except ValueError:
        pass
    return snap


def one_cycle(erp: ERPClient, cfg: dict, state: dict) -> int:
    """Un tour: heartbeat + traitement des jobs. Retourne le nb de jobs traites."""
    dev, snapshot = None, {"device": "absent"}
    try:
        port = detect_port(cfg.get("port"), state.get("port_hint"))
        dev = MCFDevice(port)
        snapshot = device_snapshot(dev)
    except DeviceUnavailable as e:
        snapshot = {"device": "absent", "detail": str(e)}
        log.warning("%s", e)

    hb = erp.heartbeat(snapshot)
    state["port_hint"] = hb.get("port_hint") or state.get("port_hint")
    if hb.get("mode") != "mcf":
        log.info("site en mode E-MCF (API): agent en veille")
        if dev:
            dev.close()
        return 0
    if dev is None:
        return 0

    done = 0
    try:
        batch = erp.pull_jobs(limit=int(cfg.get("jobs_per_cycle") or 1))
        tolerance = float(batch.get("tolerance") or 1.0)
        for job in batch.get("jobs") or []:
            name, payload = job["job"], job["payload"]
            attempts = int(job.get("attempts") or 1)
            log.info("job %s (facture %s, tentative %d)", name, job.get("invoice"), attempts)
            try:
                result = run_job(dev, payload, tolerance)
                erp.push_result(name, True, result=result)
                log.info("job %s NORMALISE - SIG=%s compteurs=%s/%s %s", name,
                         result["sig"][:12] + "...", result["fc"], result["tc"], result["ft"])
                done += 1
            except DeviceError as e:
                log.error("job %s en erreur definitive: %s", name, e)
                erp.push_result(name, False, error=str(e))
            except (DeviceUnavailable, ProtocolError) as e:
                # Transitoire: ne PAS clore le job; il repassera en file (stale) et sera rejoue.
                if attempts >= int(cfg.get("max_attempts") or 3):
                    erp.push_result(name, False,
                                    error=f"abandon apres {attempts} tentatives: {e}")
                else:
                    log.warning("job %s reporte (transitoire): %s", name, e)
                break
    finally:
        dev.close()
    return done


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    for key in ("site_url", "api_key", "api_secret"):
        if not cfg.get(key):
            raise SystemExit(f"configuration invalide: champ '{key}' manquant dans {path}")
    return cfg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Agent MCF local (DGI Compliance v4.0)")
    ap.add_argument("--config", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                     "mcf_agent_config.json"))
    ap.add_argument("--port", help="force le port serie (sinon: config, puis auto-detection)")
    ap.add_argument("--test", action="store_true", help="detection + C1h/C2h + heartbeat, puis stop")
    ap.add_argument("--once", action="store_true", help="un seul cycle puis stop")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                   "mcf_agent.log"), encoding="utf-8")])
    cfg = load_config(args.config)
    if args.port:
        cfg["port"] = args.port
    erp = ERPClient(cfg)

    if args.test:
        try:
            port = detect_port(cfg.get("port"))
            dev = MCFDevice(port)
            snap = device_snapshot(dev)
            dev.close()
            print(json.dumps(snap, indent=2, ensure_ascii=False))
        except DeviceUnavailable as e:
            print(f"MCF: {e}")
            snap = {"device": "absent", "detail": str(e)}
        hb = erp.heartbeat(snap)
        print(f"Site: mode={hb.get('mode')} (heartbeat OK)")
        return 0

    state = {}
    poll = max(2, int(cfg.get("poll_seconds") or 5))
    log.info("agent demarre (site: %s, cycle: %ss)", cfg["site_url"], poll)
    while True:
        try:
            one_cycle(erp, cfg, state)
        except KeyboardInterrupt:
            log.info("arret demande")
            return 0
        except Exception as e:
            log.error("cycle en erreur: %s", e)
        if args.once:
            return 0
        try:
            time.sleep(poll)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    sys.exit(main())
