"""Statut d'un MCF physique et test de connexion depuis un navigateur (v5.3.2).

ERPNext (cloud) ne voit pas les ports USB : seul le navigateur du PC ou le MCF est branche peut le lire
(Web Serial, Chrome / Edge). Trois ecrans le font sans effet fiscal (C1h etat, C2h connexion DGI) : la
fiche DGI MCF Device, le point de vente et la page Poste MCF, meme quand aucun poste n'est encore cree.

* `record_desk_test` : le navigateur envoie ce qu'il a lu ; le serveur compare avec ERPNext (NID, MID
  valide, NIF de la societe, taux des groupes, poste du point de vente), enregistre le resultat sur la
  fiche du MCF et trace l'evenement TEST_CONNECTIVITE. Ces valeurs ne viennent pas d'un poste signe : elles
  sont rangees a part du dernier heartbeat et ne changent ni le canal ni l'etat du MCF, sauf dans le sens
  sur (MID ou NIF different d'un MCF actif -> 'A revalider', comme a chaque lecture).
* `mcf_status` : etat consolide affiche par la fiche, le point de vente et la page Poste MCF (dernier test,
  dernier heartbeat du poste, poste en ligne, derniere connexion du MCF a la DGI, alerte au-dela d'un jour).
* `create_station` : un responsable cree le poste navigateur d'un point de vente depuis la page Poste MCF.
"""
import json

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import add_to_date, cint, flt, get_datetime, now_datetime

from dgi_compliance.fiscal import mcf_format as F
from dgi_compliance.fiscal.router import FISCAL_ROLES
from dgi_compliance.gateway.auth import KIND_BROWSER, STATION_ROLES, user_may_operate

DEVICE = "DGI MCF Device"
CLOCK_WARN_S = 120
OK, WARN, FAIL = "ok", "warn", "fail"
DGI_STATES = {"DIS": "non connecte au reseau", "CON": "connecte, aucun envoi en cours",
              "TRA": "envoi de donnees en cours", "RES": "redemarrage en cours"}


def _settings():
    from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings
    return get_settings()


def _norm(value):
    return "".join(str(value or "").split()).upper()


def _int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _check(code, label, level, message, read=None, expected=None):
    return {"code": code, "label": label, "level": level, "message": message,
            "read": "" if read is None else str(read), "expected": "" if expected is None else str(expected)}


def _expected_nif(dev):
    company = None
    if dev.get("point_of_sale"):
        company = frappe.db.get_value("DGI Point of Sale", dev.point_of_sale, "company")
    company = company or dev.get("company")
    return (frappe.db.get_value("Company", company, "tax_id") or "").strip() if company else "", company


def _stations(point_of_sale):
    """Postes (DGI Gateway) of a point de vente, with 'online' computed like fiscal.api.device_status."""
    if not point_of_sale:
        return []
    minutes = max(1, cint(_settings().get("gateway_offline_minutes") or 2))
    limit = add_to_date(now_datetime(), minutes=-minutes)
    rows = frappe.get_all("DGI Gateway", filters={"point_of_sale": point_of_sale},
                          fields=["name", "kind", "status", "last_seen", "mcf_device"], order_by="creation asc")
    for r in rows:
        r["online"] = bool(r.status == "Active" and r.last_seen and get_datetime(r.last_seen) >= limit)
    return rows


def _rate_differences(rates):
    diffs = []
    for letter, rate in sorted((rates or {}).items()):
        ours = frappe.db.get_value("DGI Tax Group", letter, "rate")
        if ours is not None and abs(flt(ours) - flt(rate)) > 0.001:
            diffs.append(f"{letter} : MCF {flt(rate):g} % / ERPNext {flt(ours):g} %")
    return diffs


def _mcf_to_server_time(mcf_now, mcf_value, server_now):
    """A date read in the MCF (its own clock) expressed in ERPNext time: server time of the reading minus the
    age measured on the MCF clock. Independent of the time zone of the site and of the drift of the MCF clock."""
    a, b = F.parse_device_datetime(mcf_now), F.parse_device_datetime(mcf_value)
    if not a or not b:
        return None
    return server_now - (a - b)


def compute_checks(obs: dict, dev, expected_nid: str | None, server_now) -> list[dict]:
    """Comparisons made by ERPNext (never by the browser) between what was read and what ERPNext expects."""
    checks = []
    if obs.get("error") or not obs.get("mid"):
        checks.append(_check("LECTURE", _("Reponse du MCF"), FAIL,
                             _("Le MCF n'a pas repondu : {0}").format(obs.get("error") or _("MID absent"))))
        return checks
    nid = F.nid_of(str(obs.get("mid")))
    port = obs.get("port") or ""
    elapsed = _int(obs.get("elapsed_ms"))
    checks.append(_check("LECTURE", _("Reponse du MCF"), OK,
                         _("Le MCF repond sur {0}{1}.").format(port or _("le port choisi"),
                                                                 f" ({elapsed} ms)" if elapsed is not None else "")))
    if expected_nid and nid != expected_nid:
        checks.append(_check("NID", _("NID"), FAIL, _("Le MCF branche n'est pas celui attendu."), nid, expected_nid))
        return checks
    if not dev:
        checks.append(_check("FICHE", _("Fiche ERPNext"), FAIL,
                             _("Ce MCF n'existe pas dans ERPNext : utilisez 'Proposer ce MCF'."), nid))
        return checks
    status_level = OK if dev.status == "Actif" else WARN
    status_msg = {"Actif": _("MCF valide : il peut recevoir des factures."),
                  "A revalider": _("MID ou NIF change : a valider de nouveau avant de recevoir des factures."),
                  "Suspendu": _("MCF suspendu : il ne recoit pas de factures."),
                  "Retire": _("MCF retire : il ne recoit pas de factures.")}.get(
        dev.status, _("MCF pas encore valide : il ne recoit pas de factures (DGI MCF Device > Valider ce MCF)."))
    checks.append(_check("STATUT", _("Etat dans ERPNext"), status_level, status_msg, dev.status))
    if dev.validated_mid:
        same = str(obs.get("mid")).strip() == dev.validated_mid
        checks.append(_check("MID", _("MID (compteur d'activation)"), OK if same else FAIL,
                             _("Identique au MID valide.") if same else
                             _("Different du MID valide : le MCF doit etre valide de nouveau."),
                             obs.get("mid"), dev.validated_mid))
    expected_nif, company = _expected_nif(dev)
    if expected_nif:
        same = _norm(obs.get("nif")) == _norm(expected_nif)
        checks.append(_check("NIF", _("NIF"), OK if same else FAIL,
                             _("NIF de la societe {0}.").format(company) if same else
                             _("Le NIF du MCF n'est pas celui de la societe {0}.").format(company),
                             obs.get("nif"), expected_nif))
    else:
        checks.append(_check("NIF", _("NIF"), WARN, _("Aucune societe avec NIF rattachee : NIF non compare."),
                             obs.get("nif")))
    diffs = _rate_differences(obs.get("tax_rates") if isinstance(obs.get("tax_rates"), dict) else {})
    groups = cint(obs.get("group_count")) or len(obs.get("tax_rates") or {})
    checks.append(_check("TAUX", _("Groupes de taxation"), WARN if diffs else OK,
                         ("; ".join(diffs) + ". " + _("Synchronisez les groupes (e-MCF) ou mettez a jour le MCF."))
                         if diffs else _("{0} groupes, taux identiques a ERPNext.").format(groups), groups))
    flags = [f for f in (obs.get("flags") or []) if f]
    if flags or obs.get("invoice_open"):
        checks.append(_check("ETAT", _("Etat du MCF (C1h)"), WARN,
                             ", ".join(flags + ([_("facture ouverte")] if obs.get("invoice_open") else []))))
    else:
        checks.append(_check("ETAT", _("Etat du MCF (C1h)"), OK, _("Aucun indicateur d'erreur, aucune facture ouverte.")))
    offset = _int(obs.get("clock_offset_s"))
    if offset is not None:
        far = abs(offset) > CLOCK_WARN_S
        checks.append(_check("HORLOGE", _("Horloge du MCF"), WARN if far else OK,
                             _("Ecart de {0} avec l'horloge de ce PC.").format(_duration(offset)) if far
                             else _("A l'heure (ecart {0} s avec ce PC).").format(offset), obs.get("dt")))
    dgi = obs.get("dgi") if isinstance(obs.get("dgi"), dict) else {}
    if dgi.get("read_error"):
        checks.append(_check("DGI", _("Connexion du MCF a la DGI (C2h)"), WARN,
                             _("Lecture C2h impossible : {0}").format(dgi.get("read_error"))))
    elif dgi:
        state = str(dgi.get("state") or "")
        sent, available = _int(dgi.get("sent")), _int(dgi.get("pending"))
        unsent = max(0, available - sent) if sent is not None and available is not None else None
        mcf_age_s = None
        a, b = F.parse_device_datetime(obs.get("dt")), F.parse_device_datetime(dgi.get("last_sync"))
        if a and b:
            mcf_age_s = int((a - b).total_seconds())
        warn_days = max(1, cint(_settings().get("mcf_sync_warning_days") or 1))
        old = mcf_age_s is None or mcf_age_s > warn_days * 86400
        level = WARN if (old or state == "DIS" or unsent) else OK
        parts = [f"{state} ({_(DGI_STATES.get(state, 'etat inconnu'))})"]
        parts.append(_("derniere connexion reussie : {0}").format(
            _("il y a {0}").format(_duration(mcf_age_s)) if mcf_age_s is not None else _("jamais")))
        if unsent is not None:
            parts.append(_("{0} transaction(s) non envoyee(s)").format(unsent))
        if old:
            parts.append(_("plus de {0} jour(s) : verifiez la connexion du MCF (carte SIM, reseau)").format(warn_days))
        checks.append(_check("DGI", _("Connexion du MCF a la DGI (C2h)"), level, " ; ".join(parts), state))
    stations = _stations(dev.point_of_sale)
    browser = [s for s in stations if s.kind == KIND_BROWSER and s.status != "Revoquee"]
    active = [s for s in stations if s.status == "Active"]
    if not dev.point_of_sale:
        checks.append(_check("POSTE", _("Poste MCF"), WARN, _("MCF rattache a aucun point de vente.")))
    elif not browser and not active:
        checks.append(_check("POSTE", _("Poste MCF"), WARN,
                             _("Aucun poste MCF pour {0} : les factures ne peuvent pas etre transmises a ce MCF. "
                               "Creez le poste (page Poste MCF ou DGI Gateway, type Poste navigateur).")
                             .format(dev.point_of_sale)))
    elif not active:
        s = browser[0]
        checks.append(_check("POSTE", _("Poste MCF"), WARN,
                             _("Poste {0} a l'etat '{1}' : enrolement a terminer.").format(s.name, _(s.status))))
    else:
        s = active[0]
        checks.append(_check("POSTE", _("Poste MCF"), OK if s.online else WARN,
                             _("Poste {0} en ligne.").format(s.name) if s.online else
                             _("Poste {0} hors ligne : ouvrez la page Poste MCF sur la caisse du MCF.").format(s.name)))
    return checks


def _duration(seconds):
    seconds = abs(int(seconds or 0))
    if seconds < 120:
        return _("{0} s").format(seconds)
    if seconds < 7200:
        return _("{0} min").format(seconds // 60)
    if seconds < 3 * 86400:
        return _("{0} h").format(seconds // 3600)
    return _("{0} jours").format(seconds // 86400)


def overall(checks):
    by_code = {c["code"]: c for c in checks}
    if any(by_code.get(code, {}).get("level") == FAIL for code in ("LECTURE", "NID")):
        return "Echec"
    if any(c["level"] == FAIL for c in checks):
        return "Ecart"
    if any(c["level"] == WARN for c in checks):
        return "Avertissement"
    return "OK"


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=60, seconds=3600)
def record_desk_test(observation=None, expected_nid=None, origin=None, gateway_id=None) -> dict:
    """'Tester la connexion' from a browser (fiche MCF, point de vente, Poste MCF, Rechercher un MCF).

    `observation`: what the page read (C1h + C2h, no fiscal effect) or {"error": ...} when the MCF did not
    answer. The comparisons are made here. Nothing is created: an unknown MCF goes through 'Proposer'."""
    from dgi_compliance.fiscal.operations import check_identity
    from dgi_compliance.gateway.events import log_event
    if not user_may_operate(frappe.session.user):
        frappe.throw(_("Le role DGI Caisse MCF ou DGI Fiscal Manager est requis pour tester un MCF."),
                     frappe.PermissionError)
    obs = frappe.parse_json(observation) if isinstance(observation, str) else (observation or {})
    if not isinstance(obs, dict):
        frappe.throw(_("Lecture du MCF invalide."))
    expected_nid = _norm(expected_nid) or None
    nid = F.nid_of(str(obs.get("mid") or "")) or expected_nid
    if gateway_id and not frappe.db.exists("DGI Gateway", gateway_id):
        gateway_id = None
    dev = frappe.get_doc(DEVICE, nid) if nid and frappe.db.exists(DEVICE, nid) else None
    if dev and dev.point_of_sale and not frappe.has_permission("DGI Point of Sale", "read", dev.point_of_sale):
        frappe.throw(_("Vous n'avez pas acces au point de vente {0}.").format(dev.point_of_sale), frappe.PermissionError)
    now = now_datetime()
    checks = compute_checks(obs, dev, expected_nid, now)
    result = overall(checks)
    origin = str(origin or "")[:80]
    summary = "; ".join(f"{c['label']} : {c['message']}" for c in checks if c["level"] != OK) or _(
        "MCF joignable, identite conforme, aucune alerte.")
    out = {"nid": nid, "known": bool(dev), "status": dev.status if dev else None, "result": result,
           "checks": checks, "summary": summary, "tested_on": str(now)}
    if not dev:
        return out
    dgi = obs.get("dgi") if isinstance(obs.get("dgi"), dict) else {}
    sent, available = _int(dgi.get("sent")), _int(dgi.get("pending"))
    values = {
        "connection_status": result, "last_connectivity_test": now,
        "last_connectivity_result": f"{result} - {summary}"[:1000],
        "last_test_by": frappe.session.user,
        "last_test_origin": " - ".join(x for x in [origin, str(obs.get("port") or "")] if x)[:140] or None,
        "test_dgi_state": (str(dgi.get("state") or "")[:3] or None) if dgi and not dgi.get("read_error") else None,
        "test_dgi_last_sync": _mcf_to_server_time(obs.get("dt"), dgi.get("last_sync"), now) if dgi else None,
        # Data fields (an Int column cannot be empty): blank when the value could not be read
        "test_dgi_unsent": str(max(0, available - sent)) if sent is not None and available is not None else None,
        "test_clock_offset_s": None if _int(obs.get("clock_offset_s")) is None else str(_int(obs.get("clock_offset_s"))),
        "connection_test_details": json.dumps({"checks": checks, "observation": obs, "origin": origin,
                                               "gateway": gateway_id}, ensure_ascii=False, default=str)[:20000],
    }
    if obs.get("mid") and not obs.get("error"):
        values["last_mid"] = str(obs.get("mid"))[:40]
    frappe.db.set_value(DEVICE, dev.name, values, update_modified=False)
    if not obs.get("error") and (not expected_nid or nid == expected_nid):
        out["identity_event"] = check_identity(dev, mid=obs.get("mid"), nif=obs.get("nif"), gateway=gateway_id)
        out["status"] = frappe.db.get_value(DEVICE, dev.name, "status")
    log_event(gateway_id, "TEST_CONNECTIVITE",
              _("Test sans effet fiscal du MCF {0} par {1} ({2}) : {3}").format(dev.name, frappe.session.user,
                                                                               origin or "-", result),
              level="Info" if result in ("OK", "Avertissement") else "Alerte", device=dev.name,
              point_of_sale=dev.point_of_sale, details={"checks": checks, "origin": origin})
    frappe.db.commit()
    return out


@frappe.whitelist()
def mcf_status(nid: str | None = None, point_of_sale: str | None = None) -> dict:
    """Consolidated state of the MCF of a fiche or of a point de vente (no call to the MCF)."""
    if not nid and point_of_sale:
        if not frappe.has_permission("DGI Point of Sale", "read", point_of_sale):
            frappe.throw(_("Permission insuffisante."), frappe.PermissionError)
        nid = frappe.db.get_value("DGI Point of Sale", point_of_sale, "mcf_device")
    nid = _norm(nid)
    roles = set(frappe.get_roles())
    base = {"can_test": bool(roles & set(STATION_ROLES)), "can_manage": bool(roles & set(FISCAL_ROLES))}
    if not nid or not frappe.db.exists(DEVICE, nid):
        base.update({"nid": nid or None, "known": False, "point_of_sale": point_of_sale,
                     "stations": _stations(point_of_sale)})
        return base
    if not frappe.has_permission(DEVICE, "read", nid):
        frappe.throw(_("Permission insuffisante."), frappe.PermissionError)
    dev = frappe.get_doc(DEVICE, nid)
    now = now_datetime()
    stations = _stations(dev.point_of_sale)
    heartbeat = {}
    seen = [get_datetime(s.last_seen) for s in stations if s.last_seen and s.mcf_device == dev.name]
    if seen:                                   # signed heartbeats of a poste (page Poste MCF or service)
        try:
            snap = json.loads(dev.last_status or "{}")
        except ValueError:
            snap = {}
        snap = snap if snap.get("gateway_version") else {}     # last_status may hold a later desk reading
        heartbeat = {"on": max(seen), "dgi_state": snap.get("dgi_state"), "tc": snap.get("tc"),
                     "dgi_last_sync": dev.dgi_last_sync, "dgi_pending": dev.dgi_pending,
                     "flags": dev.status_flags, "device_error": snap.get("device_error")}
    try:
        checks = json.loads(dev.connection_test_details or "{}").get("checks") or []
    except ValueError:
        checks = []
    test = {"result": dev.connection_status or "Non teste", "on": dev.last_connectivity_test,
            "by": dev.last_test_by, "origin": dev.last_test_origin, "summary": dev.last_connectivity_result,
            "dgi_state": dev.test_dgi_state, "dgi_last_sync": dev.test_dgi_last_sync,
            "unsent": _int(dev.test_dgi_unsent), "clock_offset_s": _int(dev.test_clock_offset_s), "checks": checks,
            "age_s": int((now - get_datetime(dev.last_connectivity_test)).total_seconds())
            if dev.last_connectivity_test else None}
    # last connection of the MCF to the DGI: the most recent of the signed heartbeat and of the last test
    syncs = [get_datetime(x) for x in (dev.dgi_last_sync, dev.test_dgi_last_sync) if x]
    last_sync = max(syncs) if syncs else None
    warn_days = max(1, cint(_settings().get("mcf_sync_warning_days") or 1))
    online = [s for s in stations if s.online]
    if online:
        indicator, label = "green", _("En ligne (poste {0})").format(online[0].name)
    elif test["on"] and test["age_s"] is not None and test["age_s"] <= 900 and test["result"] in ("OK", "Avertissement"):
        indicator, label = ("green" if test["result"] == "OK" else "orange"), _("Joignable (test il y a {0})").format(
            _duration(test["age_s"]))
    elif test["result"] in ("Ecart", "Echec"):
        indicator, label = "red", _("Dernier test : {0}").format(_(test["result"]))
    elif any(s.status == "Active" for s in stations):
        indicator, label = "red", _("Poste hors ligne")
    elif test["on"]:
        indicator, label = "gray", _("Dernier test il y a {0}").format(_duration(test["age_s"]))
    else:
        indicator, label = "gray", _("Jamais teste")
    base.update({
        "known": True, "nid": dev.name, "status": dev.status, "point_of_sale": dev.point_of_sale,
        "company": dev.company, "nif": dev.nif, "last_mid": dev.last_mid, "validated_mid": dev.validated_mid,
        "taxpayer_name": dev.taxpayer_name, "stations": stations, "heartbeat": heartbeat, "test": test,
        "dgi_last_sync": last_sync,
        "dgi_sync_age_s": int((now - last_sync).total_seconds()) if last_sync else None,
        "dgi_sync_old": (not last_sync) or (now - last_sync).total_seconds() > warn_days * 86400,
        "warn_days": warn_days, "indicator": indicator, "label": label,
        "can_create_station": base["can_manage"] and bool(dev.point_of_sale) and not any(
            s.kind == KIND_BROWSER and s.status != "Revoquee" for s in stations),
    })
    return base


@frappe.whitelist(methods=["POST"])
def create_station(point_of_sale: str) -> dict:
    """'Creer le poste MCF' (page Poste MCF): browser station of a point de vente, state 'Nouvelle'.
    The enrolment still needs a code generated by a responsible and an approval with the fingerprint."""
    from dgi_compliance.gateway.events import log_event
    frappe.only_for(FISCAL_ROLES)
    if not point_of_sale or not frappe.db.exists("DGI Point of Sale", point_of_sale):
        frappe.throw(_("Point de vente {0} introuvable.").format(point_of_sale or ""))
    existing = frappe.get_all("DGI Gateway", filters={"point_of_sale": point_of_sale, "kind": KIND_BROWSER,
                                                      "status": ["!=", "Revoquee"]}, pluck="name")
    if existing:
        return {"name": existing[0], "created": False}
    pvf = frappe.db.get_value("DGI Point of Sale", point_of_sale, ["mcf_device", "pos_code"], as_dict=True)
    base = "POSTE-" + ("".join(ch for ch in _norm(pvf.mcf_device or pvf.pos_code or point_of_sale)
                                if ch.isalnum() or ch in "-_")[:40] or "MCF")
    name, i = base, 1
    while frappe.db.exists("DGI Gateway", name):
        i += 1
        name = f"{base}-{i}"
    doc = frappe.get_doc({"doctype": "DGI Gateway", "gateway_id": name, "kind": KIND_BROWSER,
                          "point_of_sale": point_of_sale, "baudrate": 115200})
    doc.insert(ignore_permissions=True)
    log_event(doc.name, "POSTE_CREE", _("Poste MCF {0} cree pour {1} par {2} (page Poste MCF).").format(
        doc.name, point_of_sale, frappe.session.user), point_of_sale=point_of_sale, device=pvf.mcf_device)
    frappe.db.commit()
    return {"name": doc.name, "created": True}
