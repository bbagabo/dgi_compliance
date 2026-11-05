"""Discovery and registration of a physical MCF (v5.3.0, livrable B section 7).

ERPNext in the cloud does not see the USB ports of the shops: the MCF is read on the cash desk where
it is plugged in (page Poste MCF, no fiscal effect: C1h, 2Bh, C2h), proposed to ERPNext (state
'Detecte'), then validated by a DGI Fiscal Manager -- another person than the one who proposed it when
the two-person rule is on. Nothing is activated without that explicit validation; an MCF never read
cannot become Actif (no NIF to check). The values read come from the cash desk browser: they only
prepare the validation, and NID / NIF are read again at every signed fiscal operation.
"""
import json

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, now_datetime

from dgi_compliance.fiscal import mcf_format as F
from dgi_compliance.fiscal.router import CHANNEL_EMCF, CHANNEL_MCF, FISCAL_ROLES, other_channel

DEVICE = "DGI MCF Device"
VALIDATABLE = ("Detecte", "Declare", "A revalider")


def _norm(value):
    return "".join(str(value or "").split()).upper()


def _settings():
    from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings
    return get_settings()


def record_observation(observation: dict, by: str, gateway: str | None = None) -> dict:
    """Store what was read in an MCF. Creates a 'Detecte' device when the NID is unknown; never activates."""
    from dgi_compliance.fiscal.operations import check_identity
    from dgi_compliance.gateway.events import log_event
    obs = observation or {}
    mid = str(obs.get("mid") or "").strip()
    nid = F.nid_of(mid) or _norm(obs.get("nid"))
    if not nid:
        frappe.throw(_("Lecture du MCF incomplete : NID / MID absent."))
    values = {"last_mid": mid or None, "nif": str(obs.get("nif") or "").strip() or None,
              "taxpayer_name": str(obs.get("taxpayer_name") or "").strip()[:140] or None,
              "taxpayer_address": str(obs.get("taxpayer_address") or "").strip()[:500] or None,
              "observed_on": now_datetime(),
              "last_status": json.dumps(obs, ensure_ascii=False, default=str)[:20000], "last_status_on": now_datetime()}
    if isinstance(obs.get("tax_rates"), dict) and obs["tax_rates"]:
        values["tax_rates"] = json.dumps(obs["tax_rates"], sort_keys=True)
    created = False
    if frappe.db.exists(DEVICE, nid):
        dev = frappe.get_doc(DEVICE, nid)
        if dev.status == "Actif":
            check_identity(dev, mid=mid, nif=values["nif"], gateway=gateway)
            values.pop("last_status_on", None)
        elif dev.status in ("Detecte", "Declare", "A revalider") and not dev.discovered_by:
            values.update({"discovered_by": by, "discovered_on": now_datetime(), "discovered_gateway": gateway})
        frappe.db.set_value(DEVICE, nid, {k: v for k, v in values.items() if v is not None or k == "nif"},
                            update_modified=False)
    else:
        doc = frappe.get_doc(dict(values, doctype=DEVICE, nid=nid, status="Detecte", discovered_by=by,
                                  discovered_on=now_datetime(), discovered_gateway=gateway,
                                  manufacturer=obs.get("manufacturer") or None, model=obs.get("model") or None))
        doc.flags.dgi_registration = True
        doc.insert(ignore_permissions=True)
        created = True
    status = frappe.db.get_value(DEVICE, nid, "status")
    log_event(gateway, "MCF_PROPOSE", _("MCF {0} (MID {1}, NIF {2}) lu par {3} : etat {4}.").format(
        nid, mid or "?", values["nif"] or "?", by, status), level="Info", device=nid,
        details={"observation": obs, "created": created})
    return {"nid": nid, "status": status, "created": created}


def comparison(nid: str, point_of_sale: str | None = None) -> list[dict]:
    """Values read in the MCF next to the values expected for the point de vente."""
    dev = frappe.get_doc(DEVICE, nid)
    pvf_name = point_of_sale or dev.point_of_sale
    pvf = frappe.get_doc("DGI Point of Sale", pvf_name) if pvf_name and frappe.db.exists("DGI Point of Sale", pvf_name) else None
    company = pvf.company if pvf else dev.company
    expected_nif = (frappe.db.get_value("Company", company, "tax_id") or "").strip() if company else ""
    company_name = frappe.db.get_value("Company", company, "company_name") if company else ""
    expected_addr = ", ".join([x for x in [(pvf.pos_address or "").strip() if pvf else "", (pvf.pos_city or "").strip() if pvf else ""] if x])
    other = frappe.db.get_value("DGI Point of Sale", {"mcf_device": nid, "is_active": 1, "name": ["!=", pvf_name or ""]})
    rows = [
        {"field": "NIF", "read": dev.nif or "", "expected": expected_nif, "blocking": 1,
         "ok": bool(dev.nif) and bool(expected_nif) and _norm(dev.nif) == _norm(expected_nif)},
        {"field": _("Raison sociale"), "read": dev.taxpayer_name or "", "expected": company_name or "", "blocking": 0,
         "ok": _norm(dev.taxpayer_name) == _norm(company_name)},
        {"field": _("Adresse"), "read": dev.taxpayer_address or "", "expected": expected_addr, "blocking": 0,
         "ok": not expected_addr or _norm(expected_addr) in _norm(dev.taxpayer_address) or _norm(dev.taxpayer_address) == _norm(expected_addr)},
        {"field": "NID", "read": dev.name, "expected": _("non rattache a un autre point de vente"), "blocking": 1,
         "ok": not other},
        {"field": "MID", "read": dev.last_mid or "", "expected": dev.validated_mid or _("premiere validation"),
         "blocking": 1, "ok": bool(dev.last_mid) and F.nid_of(dev.last_mid) == dev.name},
    ]
    return rows


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=20, seconds=3600)
def propose_from_browser(observation=None, point_of_sale=None) -> dict:
    """'Rechercher un MCF (sur ce PC)' in DGI Compliance Settings (v5.3.1).

    The MCF is read by the browser of the computer where it is plugged in (Web Serial: C1h, 2Bh, C2h,
    no fiscal effect), exactly as on the Poste MCF page, then registered 'Detecte'. Never activated here:
    a DGI Fiscal Manager validates it afterwards (another person when the two-person rule is on), and
    NID / NIF are read again at every fiscal operation. Reserved to DGI Fiscal Manager / System Manager."""
    from dgi_compliance.gateway.events import log_event
    frappe.only_for(FISCAL_ROLES)
    obs = frappe.parse_json(observation) if isinstance(observation, str) else (observation or {})
    if not isinstance(obs, dict):
        frappe.throw(_("Lecture du MCF invalide."))
    nid = F.nid_of(str(obs.get("mid") or "")) or _norm(obs.get("nid"))
    if point_of_sale:
        if not frappe.db.exists("DGI Point of Sale", point_of_sale):
            frappe.throw(_("Point de vente {0} introuvable.").format(point_of_sale))
        other = frappe.db.get_value("DGI Point of Sale", {"mcf_device": nid, "is_active": 1,
                                                          "name": ["!=", point_of_sale]}) if nid else None
        if other and frappe.db.get_value(DEVICE, nid, "status") == "Actif":
            log_event(None, "MCF_REFUSE", _("MCF {0} propose pour {1} alors qu'il est actif sur {2}.")
                      .format(nid, point_of_sale, other), level="Alerte", device=nid,
                      point_of_sale=point_of_sale, details={"observation": obs, "by": frappe.session.user})
            frappe.db.commit()
            frappe.throw(_("Ce MCF (NID {0}) est deja actif sur un autre point de vente ({1}).").format(nid, other))
        obs["proposed_for"] = point_of_sale
    obs["read_by"] = "browser-desk"
    res = record_observation(obs, frappe.session.user, gateway=None)
    frappe.db.commit()
    return res


@frappe.whitelist()
def device_comparison(nid: str, point_of_sale: str | None = None) -> dict:
    frappe.only_for(FISCAL_ROLES)
    dev = frappe.get_doc(DEVICE, nid)
    return {"rows": comparison(nid, point_of_sale), "status": dev.status, "discovered_by": dev.discovered_by,
            "point_of_sale": point_of_sale or dev.point_of_sale}


@frappe.whitelist(methods=["POST"])
def validate_device(nid: str, point_of_sale: str, accept_differences: str | None = None,
                    primary_channel: str | None = None, priority_reason: str | None = None) -> dict:
    """Explicit validation of a detected / declared MCF by a DGI Fiscal Manager."""
    from dgi_compliance.gateway.events import log_event
    frappe.only_for(FISCAL_ROLES)
    frappe.db.sql(f"select name from `tab{DEVICE}` where name=%s for update", nid)
    dev = frappe.get_doc(DEVICE, nid)
    if dev.status not in VALIDATABLE:
        frappe.throw(_("Le MCF {0} est a l'etat {1} : rien a valider.").format(nid, dev.status))
    if cint(_settings().get("station_approval_two_people")) and dev.discovered_by == frappe.session.user:
        frappe.throw(_("Regle des deux personnes : le MCF doit etre valide par une autre personne que celle qui l'a "
                       "propose ({0}).").format(dev.discovered_by))
    if not dev.last_mid or not dev.nif:
        frappe.throw(_("Le MCF {0} n'a jamais ete lu : branchez-le sur la caisse et lisez-le depuis la page Poste MCF "
                       "avant de le valider (NIF a controler).").format(nid))
    pvf = frappe.get_doc("DGI Point of Sale", point_of_sale)
    if dev.company and pvf.company and dev.company != pvf.company:
        frappe.throw(_("Le MCF {0} est declare pour la societe {1}, le point de vente pour {2}.").format(nid, dev.company, pvf.company))
    if pvf.mcf_device and pvf.mcf_device != nid and frappe.db.get_value(DEVICE, pvf.mcf_device, "status") == "Actif":
        frappe.throw(_("Le point de vente {0} a deja un MCF actif ({1}) : retirez-le d'abord.").format(pvf.name, pvf.mcf_device))
    rows = comparison(nid, point_of_sale)
    blocking = [r for r in rows if r["blocking"] and not r["ok"]]
    if blocking:
        log_event(None, "MCF_REFUSE", _("Validation du MCF {0} refusee : {1}.").format(
            nid, "; ".join(f"{r['field']} lu {r['read'] or '-'} / attendu {r['expected'] or '-'}" for r in blocking)),
            level="Alerte", point_of_sale=point_of_sale, device=nid, details={"rows": rows})
        frappe.db.commit()
        frappe.throw(_("Validation refusee : {0}.").format(
            "; ".join(f"{r['field']} lu {r['read'] or '-'} / attendu {r['expected'] or '-'}" for r in blocking)))
    differences = [r for r in rows if not r["ok"]]
    if differences and not (accept_differences or "").strip():
        frappe.throw(_("Ecarts a accepter explicitement (motif) : {0}.").format(
            "; ".join(f"{r['field']} lu {r['read'] or '-'} / attendu {r['expected'] or '-'}" for r in differences)))
    if primary_channel and primary_channel not in (CHANNEL_EMCF, CHANNEL_MCF):
        frappe.throw(_("Canal prioritaire invalide."))
    dev.flags.dgi_registration = True
    dev.update({"status": "Actif", "point_of_sale": pvf.name, "company": pvf.company, "validated_by": frappe.session.user,
                "validated_on": now_datetime(), "validated_mid": dev.last_mid, "nif_check": "Concordant",
                "accepted_differences": (accept_differences or "").strip()[:1000] or None})
    dev.save(ignore_permissions=True)
    pvf.mcf_device = nid
    if primary_channel:
        pvf.primary_channel = primary_channel
        pvf.priority_reason = priority_reason
    pvf.save()
    log_event(None, "MCF_VALIDE", _("MCF {0} valide par {1} pour {2} (MID {3}).").format(
        nid, frappe.session.user, pvf.name, dev.validated_mid), level="Alerte", point_of_sale=pvf.name, device=nid,
        details={"rows": rows, "accepted_differences": accept_differences})
    return {"ok": True, "status": "Actif", "point_of_sale": pvf.name}


@frappe.whitelist(methods=["POST"])
def refuse_device(nid: str, reason: str) -> dict:
    from dgi_compliance.gateway.events import log_event
    frappe.only_for(FISCAL_ROLES)
    if not (reason or "").strip():
        frappe.throw(_("Indiquez le motif du refus."))
    dev = frappe.get_doc(DEVICE, nid)
    if dev.status == "Actif":
        frappe.throw(_("Un MCF actif se suspend ou se retire, il ne se refuse pas."))
    note = f"{now_datetime()} refus par {frappe.session.user} : {reason.strip()}"
    frappe.db.set_value(DEVICE, nid, "notes", ((dev.notes or "") + "\n" + note).strip()[:4000], update_modified=False)
    log_event(None, "MCF_REFUSE", _("MCF {0} refuse par {1} : {2}").format(nid, frappe.session.user, reason),
              level="Alerte", device=nid)
    return {"ok": True}


def record_connectivity(nid: str, result: dict, gateway: str | None, by: str) -> dict:
    from dgi_compliance.gateway.events import log_event
    ok = bool((result or {}).get("ok"))
    text = (str((result or {}).get("summary") or "") or (_("OK") if ok else _("echec")))[:1000]
    frappe.db.set_value(DEVICE, nid, {"last_connectivity_test": now_datetime(),
                                      "last_connectivity_result": f"{'OK' if ok else 'ECHEC'} - {text}",
                                      "connection_status": "OK" if ok else "Echec", "last_test_by": by,
                                      "last_test_origin": (_("Poste {0}").format(gateway) if gateway else None)},
                        update_modified=False)
    log_event(gateway, "TEST_CONNECTIVITE", _("Test sans effet fiscal du MCF {0} par {1} : {2}").format(nid, by, text),
              level="Info" if ok else "Alerte", device=nid, details=result)
    return {"ok": ok}


@frappe.whitelist(methods=["POST"])
def reroute_rejected(operation: str) -> dict:
    """A DGI Fiscal Manager sends a REJECTED operation to the other channel (the refusal proves that the
    invoice was not fiscalized). Only drafts not normalized, without any active operation."""
    from dgi_compliance.edef.tasks import normalize_invoice
    from dgi_compliance.fiscal.operations import active_operation
    frappe.only_for(FISCAL_ROLES)
    op = frappe.get_doc("DGI Fiscal Operation", operation)
    if op.status != "Rejected":
        frappe.throw(_("Seule une operation refusee peut etre renvoyee sur l'autre canal."))
    doc = frappe.get_doc("Sales Invoice", op.sales_invoice)
    if doc.docstatus != 0 or doc.get("custom_dgi_status") == "Normalized":
        frappe.throw(_("La facture {0} n'est plus un brouillon a normaliser.").format(doc.name))
    if active_operation(doc.name):
        frappe.throw(_("La facture {0} a deja une operation en cours.").format(doc.name))
    target = other_channel(op.channel or CHANNEL_MCF)
    frappe.flags.dgi_force_channel = target
    try:
        res = normalize_invoice(doc)
    finally:
        frappe.flags.dgi_force_channel = None
    from dgi_compliance.gateway.events import log_event
    log_event(None, "RENVOI_MANUEL", _("{0} refusee sur {1} : facture {2} renvoyee sur {3} par {4}.").format(
        op.name, op.channel, doc.name, target, frappe.session.user), level="Alerte", point_of_sale=op.point_of_sale)
    return res or {"ok": True}
