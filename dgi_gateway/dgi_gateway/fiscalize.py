"""Execution of one fiscal operation on the MCF, with journal and reconciliation.

Outcomes returned to ERPNext:
  normalized  security elements obtained (from the 38h answer, or recovered through C1h)
  rejected    the invoice is CERTAINLY not normalized (error before 38h, 38h refused, or proof
              through C1h); `retryable` tells whether a new attempt makes sense
  uncertain   38h may have been executed and the MCF state could not be read
  done        technical operation (RESYNC) executed

Reconciliation relies on C1h, which only knows the LAST invoice of the MCF. It is conclusive
because the gateway (and the ERPNext barrier) never start another invoice while an operation
of the same MCF is uncertain.
"""
import logging

from . import protocol as P
from .device import DeviceRefused, LinkError, PendingTimeout

log = logging.getLogger("dgi_gateway.fiscalize")

# device text (upper case, no accents) -> (code, retryable, message)
REFUSAL_CODES = (
    ("MONTANT N'EST PAS VALIDE", "MONTANT_INVALIDE", False,
     "Le total TTC ou TVA envoye ne correspond pas aux montants enregistres par le MCF."),
    ("IFU", "TAUX_CHANGE_REFUSE", False,
     "Le MCF a refuse le taux de change a l'ouverture de la facture. La facture n'est pas normalisee "
     "sans son taux (regle v5.0.5 : le taux saisi sur la facture est transmis)."),
    ("PAS PU ETRE VERIFI", "AVOIR_NON_VERIFIABLE", True,
     "Le MCF n'a pas pu verifier l'avoir aupres du serveur DGI (pas de reseau). Reessayez plus tard."),
    ("NIF INVALIDE", "NIF_INVALIDE", False, "Le MCF a refuse le NIF du vendeur ou les donnees du client."),
    ("DEJA UTILISE", "NUMERO_DEJA_UTILISE", False, "Ce numero est celui de la derniere facture normalisee par le MCF."),
    ("MEMOIRE PLEINE", "MEMOIRE_PLEINE", False, "La memoire du MCF est pleine : contactez le fournisseur."),
    ("GROUPE DE TAXATION", "GROUPE_INCONNU_MCF", False,
     "Le MCF ne connait pas ce groupe de taxation : mise a jour du MCF necessaire."),
    ("MODE DE PAIEMENT", "PAIEMENT_INCONNU_MCF", False,
     "Le MCF refuse ce mode de paiement (credit : verifiez la lettre configuree dans ERPNext)."),
)


def _fold(text: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in t if not unicodedata.combining(c)).upper()


def classify_refusal(text: str):
    folded = _fold(text)
    for needle, code, retryable, message in REFUSAL_CODES:
        if needle in folded:
            return code, retryable, message
    return "REFUS_MCF", False, "Le MCF a refuse la commande."


class WrongDevice(Exception):
    def __init__(self, code: str, message: str, status: dict | None = None):
        self.code, self.status = code, status or {}
        super().__init__(message)


class Executor:
    def __init__(self, device, journal, *, tolerance: float = 1.0, pending_timeout: float = 90.0,
                 pending_interval: float = 1.5):
        self.dev = device
        self.journal = journal
        self.tolerance = float(tolerance)
        self.pending_timeout = pending_timeout
        self.pending_interval = pending_interval

    # ------------------------------------------------------------------ helpers
    def _result(self, op, outcome, *, data=None, code=None, message=None, retryable=False,
                device_text=None, proven=False):
        res = {"operation_id": op["operation_id"], "lease_token": op.get("lease_token"),
               "op_type": op.get("op_type") or "NORMALIZE", "outcome": outcome,
               "data": data or {}, "steps": list(self.dev.exchanges)[-60:]}
        if code or message:
            res["error"] = {"code": code or "ERREUR", "message": message or "", "retryable": bool(retryable),
                            "device_text": device_text or ""}
        if proven:
            res["proven_not_normalized"] = True
        return res

    def check_device(self, expected_nid: str | None, expected_nif: str | None) -> dict:
        st = self.dev.status()
        if not st.get("mid"):
            raise WrongDevice("ETAT_ILLISIBLE", "Reponse C1h incomplete : identite du MCF inconnue.", st)
        if expected_nid and st["nid"] != expected_nid:
            raise WrongDevice("NID_INATTENDU",
                              f"MCF branche : NID {st['nid']} ; ce poste est autorise pour le NID {expected_nid}.", st)
        if expected_nif and st["nif"] != expected_nif:
            raise WrongDevice("NIF_INATTENDU",
                              f"Le MCF est configure pour le NIF {st['nif']}, la societe a le NIF {expected_nif}.", st)
        blocking = [f for f in st.get("flags") or [] if f in P.BLOCKING_FLAGS]
        if blocking:
            raise WrongDevice("MCF_BLOQUE", "Etat du MCF bloquant : " + ", ".join(blocking), st)
        return st

    @staticmethod
    def _security_data(raw: dict, *, machine_mv=None, machine_mt=None, recovered=False, source="38h"):
        data = dict(raw)
        data["sig"] = (data.get("sig") or "").replace("-", "").upper()
        data["qr"] = P.qr_content(data.get("mid", ""), data["sig"], data.get("nif", ""), data.get("dt", ""))
        data["recovered"] = recovered
        data["source"] = source
        if machine_mv is not None:
            data["machine_mv"], data["machine_mt"] = machine_mv, machine_mt
        return data

    def _from_status(self, st: dict, inv_type: str, opened: dict | None) -> dict:
        """Security elements of the LAST invoice of the MCF, read through C1h."""
        fc = (opened or {}).get("fc")
        if not fc:
            fc = st.get("frc") if inv_type in P.CREDIT_TYPES else st.get("fvc")
        raw = {"fc": fc, "tc": st.get("tc"), "ft": st.get("dft") or inv_type, "dt": st.get("dfdt"),
               "mid": st.get("mid"), "nif": st.get("nif"), "fn": st.get("dfn"), "sig": st.get("dfs")}
        return self._security_data(raw, machine_mv=P.to_float(st.get("dmv")), recovered=True, source="C1h")

    def _cancel_quietly(self) -> bool:
        try:
            text = self.dev.cancel()
            return "ANNUL" in _fold(text) or not P.invoice_open(self.dev.last_sta)
        except Exception as e:  # noqa: BLE001 - best effort, the original error is reported
            log.warning("annulation de la facture ouverte impossible : %s", e)
            return False

    # ------------------------------------------------------------------ NORMALIZE
    def normalize(self, op: dict) -> dict:
        self.dev.exchanges = []
        inv = op["invoice"]
        dev_info = op.get("device") or {}
        entry = self.journal.record_received(op)
        if entry["state"] in ("DONE", "REJECTED") and entry.get("result"):
            return entry["result"]                                  # same lease delivered twice
        if entry["state"] in ("OPENING", "FINALIZING", "UNCERTAIN"):
            return self.check(op)                                   # interrupted earlier: reconcile first
        previous = self.journal.find_done_by_fn(inv["fn"])
        if previous and previous["op_id"] != op["operation_id"] and previous.get("result"):
            res = self._result(op, "normalized", data=dict(previous["result"]["data"], replay_of=previous["op_id"]))
            self.journal.set_state(op["operation_id"], "DONE", result=res)
            return res

        try:
            st = self.check_device(dev_info.get("nid"), dev_info.get("nif") or inv.get("nif"))
        except WrongDevice as e:
            res = self._result(op, "rejected", code=e.code, message=str(e), retryable=True, proven=True)
            self.journal.set_state(op["operation_id"], "REJECTED", result=res)
            return res
        except LinkError as e:
            res = self._result(op, "rejected", code="LIEN_MCF", message=str(e), retryable=True, proven=True)
            self.journal.set_state(op["operation_id"], "REJECTED", result=res)
            return res

        if st.get("dfn") and st["dfn"] == inv["fn"]:
            res = self._result(op, "normalized", data=self._from_status(st, inv["invoice_type"], None))
            self.journal.set_state(op["operation_id"], "DONE", result=res)
            return res
        if st.get("invoice_open"):
            log.warning("facture restee ouverte sur le MCF : annulation avant de commencer")
            self._cancel_quietly()
        known = st.get("tax_rates") or {}
        for it in inv["items"]:
            letter = it["tax_group"]
            if known and letter not in known:
                res = self._result(op, "rejected", code="GROUPE_INCONNU_MCF", proven=True,
                                   message=(f"Le MCF ne connait que les groupes {''.join(sorted(known))} : le groupe "
                                            f"{letter} demande la mise a jour du MCF (groupes FACNO v2.0)."))
                self.journal.set_state(op["operation_id"], "REJECTED", result=res)
                return res
            if letter in st.get("tax_rates", {}) and abs(st["tax_rates"][letter] - float(it["tax_rate"])) > 0.001:
                res = self._result(op, "rejected", code="TAUX_DIFFERENTS", proven=True,
                                   message=f"Groupe {letter} : taux du MCF {st['tax_rates'][letter]:.2f} %, "
                                           f"taux ERPNext {float(it['tax_rate']):.2f} %.")
                self.journal.set_state(op["operation_id"], "REJECTED", result=res)
                return res

        self.journal.set_state(op["operation_id"], "OPENING")
        try:
            self.dev.set_client(inv.get("client"))
            opened = self.dev.open_invoice(inv)
            self.journal.set_state(op["operation_id"], "OPENING", opened_counters=opened)
            for it in inv["items"]:
                self.dev.add_item(it)
            for lid in sorted((inv.get("comments") or {})):
                self.dev.set_comment(lid, inv["comments"][lid])
            sub = self.dev.subtotal()
            exp = inv.get("totals") or {}
            if abs(sub["mv"] - float(exp.get("mv") or 0)) > self.tolerance or \
                    abs(sub["mt"] - float(exp.get("mt") or 0)) > self.tolerance:
                self._cancel_quietly()
                res = self._result(op, "rejected", code="ECART_TOTAUX", proven=True,
                                   message=(f"Totaux du MCF TTC {sub['mv']:.2f} / TVA {sub['mt']:.2f} ; "
                                            f"ERPNext TTC {float(exp.get('mv') or 0):.2f} / TVA {float(exp.get('mt') or 0):.2f} "
                                            f"(tolerance {self.tolerance:g})."),
                                   data={"machine": sub})
                self.journal.set_state(op["operation_id"], "REJECTED", result=res)
                return res
            payments = inv.get("payments") or []
            if payments:
                for p in payments:
                    self.dev.pay(p["letter"], p["amount"])
            else:
                self.dev.pay()
        except DeviceRefused as e:
            code, retryable, message = classify_refusal(e.device_text)
            if code == "NUMERO_DEJA_UTILISE":
                return self.check(op)
            self._cancel_quietly()
            res = self._result(op, "rejected", code=code, message=f"{message} ({e.command})",
                               retryable=retryable, device_text=e.device_text, proven=True)
            self.journal.set_state(op["operation_id"], "REJECTED", result=res)
            return res
        except (LinkError, P.ProtocolError) as e:
            self._cancel_quietly()
            res = self._result(op, "rejected", code="LIEN_MCF", retryable=True, proven=True,
                               message=f"Liaison interrompue avant la normalisation (38h non envoyee) : {e}")
            self.journal.set_state(op["operation_id"], "REJECTED", result=res)
            return res

        self.journal.set_state(op["operation_id"], "FINALIZING")
        try:
            raw = self.dev.finalize(sub["mv"], sub["mt"], expected_fn=inv["fn"],
                                    pending_timeout=self.pending_timeout, pending_interval=self.pending_interval)
        except DeviceRefused as e:
            code, retryable, message = classify_refusal(e.device_text)
            self._cancel_quietly()
            res = self._result(op, "rejected", code=code, message=message, retryable=retryable,
                               device_text=e.device_text, proven=True)
            self.journal.set_state(op["operation_id"], "REJECTED", result=res)
            return res
        except PendingTimeout as e:
            if self._cancel_quietly():
                res = self._result(op, "rejected", code="NORMALISATION_EN_ATTENTE", retryable=True, proven=True,
                                   message=f"Le MCF attend toujours le serveur DGI ({e}). Facture annulee sur le MCF.")
                self.journal.set_state(op["operation_id"], "REJECTED", result=res)
                return res
            return self.check(op)
        except (LinkError, P.ProtocolError) as e:
            log.warning("liaison perdue pendant 38h (%s) : rapprochement par C1h", e)
            return self.check(op)

        data = self._security_data(raw, machine_mv=sub["mv"], machine_mt=sub["mt"])
        if data.get("fn") != inv["fn"] or (dev_info.get("nid") and P.nid_of(data.get("mid")) != dev_info["nid"]):
            res = self._result(op, "uncertain", data=data, code="REPONSE_INCOHERENTE",
                               message="La reponse 38h ne correspond pas a la facture ou au MCF attendu.")
            self.journal.set_state(op["operation_id"], "UNCERTAIN", result=res)
            return res
        res = self._result(op, "normalized", data=data)
        self.journal.set_state(op["operation_id"], "DONE", result=res)
        return res

    # ------------------------------------------------------------------ CHECK (reconciliation)
    def check(self, op: dict) -> dict:
        """Decide, through C1h, whether the invoice of `op` was normalized."""
        self.dev.exchanges = []
        entry = self.journal.get(op["operation_id"])
        if entry and entry["state"] == "DONE" and entry.get("result"):
            return entry["result"]
        fn = (op.get("invoice") or {}).get("fn") or op.get("fn") or (entry or {}).get("fn")
        inv_type = (op.get("invoice") or {}).get("invoice_type") or op.get("invoice_type") or "FV"
        dev_info = op.get("device") or {}
        if not entry:
            self.journal.record_received(dict(op, op_type=op.get("op_type") or "CHECK", fn=fn))
            entry = self.journal.get(op["operation_id"])
        try:
            st = self.check_device(dev_info.get("nid"), dev_info.get("nif"))
        except (LinkError, WrongDevice) as e:
            res = self._result(op, "uncertain", code=getattr(e, "code", "LIEN_MCF"),
                               message=f"Etat du MCF illisible, rapprochement impossible pour l'instant : {e}")
            self.journal.set_state(op["operation_id"], "UNCERTAIN", result=res)
            return res
        if st.get("dfn") and st["dfn"] == fn:
            res = self._result(op, "normalized", data=self._from_status(st, inv_type, entry.get("opened_counters")))
            self.journal.set_state(op["operation_id"], "DONE", result=res)
            return res
        if st.get("invoice_open"):
            self._cancel_quietly()
        res = self._result(op, "rejected", code="NON_NORMALISEE", retryable=True, proven=True,
                           message=(f"Rapprochement C1h : la derniere facture du MCF est {st.get('dfn') or 'aucune'}, "
                                    f"la facture {fn} n'a pas ete normalisee."))
        self.journal.set_state(op["operation_id"], "REJECTED", result=res)
        return res

    # ------------------------------------------------------------------ RESYNC (exigence 6.2)
    def resync(self, op: dict) -> dict:
        self.dev.exchanges = []
        try:
            st = self.dev.server_status(restart=True)
            return self._result(op, "done", data={"server_status": st})
        except LinkError as e:
            return self._result(op, "done", data={"restart_sent": True},
                                message=f"Redemarrage demande ; le MCF ne repond plus le temps de redemarrer ({e}).",
                                code="REDEMARRAGE")

    def execute(self, op: dict) -> dict:
        kind = (op.get("op_type") or "NORMALIZE").upper()
        if kind == "CHECK":
            return self.check(op)
        if kind == "RESYNC":
            return self.resync(op)
        return self.normalize(op)
