"""Fiscal routing (v5.3.0): invoice -> point de vente fiscal (PVF) -> channel -> equipment.

Livrable B, sections 4 and 5. Nothing is chosen silently: every source that designates a PVF is
read, contradictions block the submission (a DGI Fiscal Manager may arbitrate with a reason), and
the decision (sources, rules, channel, equipment) is returned with the route so that it can be
stored on the operation and summarized on the invoice.

Point de vente (section 4)
  emission sources: the PVF entered on the invoice by a person (a value filled in by ERPNext is
  recomputed), the POS Profile, the Branch (one active PVF per company + branch); delivery sources:
  the warehouses (only when `use_warehouse_mapping` is ticked; a group warehouse covers its
  children). Emission decides (choice C2); warehouses decide only when there is no emission source.
Canal (section 5)
  priority = PVF override (`primary_channel`) or the setting `primary_channel`; a manual switch in
  force (`forced_switch`) imposes its channel; an open automatic fallback sends to the other
  channel; the chosen channel must be equipped, otherwise the other one (if equipped) is used.
The channel is frozen in the operation; a fallback creates a new operation (fiscal.fallback).
"""
import json
from dataclasses import dataclass, field

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

CHANNEL_EMCF = "e-MCF"
CHANNEL_MCF = "MCF"
CHANNELS = (CHANNEL_EMCF, CHANNEL_MCF)
POLICY_AUTO = "Automatique"
POLICY_NONE = "Aucune"
FISCAL_ROLES = ("System Manager", "DGI Fiscal Manager")

# point de vente rules
EMISSION, EMISSION_CENTRALISEE, ENTREPOT, ARBITRAGE = "EMISSION", "EMISSION_CENTRALISEE", "ENTREPOT", "ARBITRAGE"
DEFAUT_PARAMETRES, UNIQUE_SOCIETE, MODE_GLOBAL = "DEFAUT_PARAMETRES", "UNIQUE_SOCIETE", "MODE_GLOBAL"
CONFLIT_SOURCES, CONFLIT_ENTREPOTS, AUCUNE_SOURCE = "CONFLIT_SOURCES", "CONFLIT_ENTREPOTS", "AUCUNE_SOURCE"
SOCIETE_DIFFERENTE, PVF_INACTIF = "SOCIETE_DIFFERENTE", "PVF_INACTIF"
# channel rules
PRIORITE_PARAMETRES, PRIORITE_POINT_DE_VENTE = "PRIORITE_PARAMETRES", "PRIORITE_POINT_DE_VENTE"
BASCULE_MANUELLE, SECOURS_AUTO, RENVOI_MANUEL = "BASCULE_MANUELLE", "SECOURS_AUTO", "RENVOI_MANUEL"
PRIORITAIRE_NON_EQUIPE, AUCUN_CANAL = "PRIORITAIRE_NON_EQUIPE", "AUCUN_CANAL"

SOURCE_LABELS = {"facture": "Facture", "pos_profile": "POS Profile", "agence": "Agence", "entrepot": "Entrepot"}


class RoutingError(frappe.ValidationError):
    def __init__(self, message, code=None, decision=None):
        super().__init__(message)
        self.code = code
        self.decision = decision or {}


@dataclass
class Route:
    point_of_sale: str | None
    channel: str
    device: str | None = None          # DGI MCF Device name (= NID) for the MCF channel
    emcf_token: str | None = None      # e-MCF token of the PVF (None = global token)
    nif: str | None = None
    nim: str | None = None
    rules: list = field(default_factory=list)
    decision: dict = field(default_factory=dict)
    alternate: "Route | None" = None   # route of the other channel when the automatic fallback applies
    fallback_reason: str | None = None  # set when the route IS a fallback (SECOURS_AUTO)
    open_fallback: dict | None = None   # {"from","to","reason"}: proven non-reception known at routing time

    @property
    def rule(self) -> str:
        return " > ".join(self.rules)

    def explanation(self) -> str:
        return explain(self.decision)


# --------------------------------------------------------------------------- helpers
def get_settings_doc():
    from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings
    return get_settings()


def default_channel(settings=None) -> str:
    """Canal prioritaire of DGI Compliance Settings (e-MCF by default)."""
    settings = settings or get_settings_doc()
    value = settings.get("primary_channel")
    if value in CHANNELS:
        return value
    return CHANNEL_MCF if (settings.get("communication_mode") or "") == "MCF Physique (USB)" else CHANNEL_EMCF


def other_channel(channel: str) -> str:
    return CHANNEL_MCF if channel == CHANNEL_EMCF else CHANNEL_EMCF


def is_fiscal_manager(user=None) -> bool:
    roles = set(frappe.get_roles(user or frappe.session.user))
    return bool(roles & set(FISCAL_ROLES))


def _has_field(doctype, fieldname):
    try:
        return bool(frappe.get_meta(doctype).has_field(fieldname))
    except Exception:
        return False


def company_nif(company):
    return (frappe.db.get_value("Company", company, "tax_id") or "").strip() if company else ""


def pvf_of_branch(company, branch):
    if not branch:
        return None
    rows = frappe.get_all("DGI Point of Sale", filters={"branch": branch, "is_active": 1},
                          fields=["name", "company"], order_by="name asc")
    rows = [r for r in rows if not company or not r.company or r.company == company]
    return rows[0].name if rows else None


def pvf_of_warehouse(warehouse):
    """PVF of the warehouse or of its nearest ancestor that has one."""
    if not warehouse or not _has_field("Warehouse", "dgi_point_of_sale"):
        return None
    bounds = frappe.db.get_value("Warehouse", warehouse, ["lft", "rgt"])
    if not bounds or bounds[0] is None:
        return frappe.db.get_value("Warehouse", warehouse, "dgi_point_of_sale")
    r = frappe.db.sql("""select dgi_point_of_sale from `tabWarehouse`
                         where lft <= %s and rgt >= %s and ifnull(dgi_point_of_sale, '') != ''
                         order by lft desc limit 1""", bounds)
    return r[0][0] if r else None


def _warehouses(doc):
    seen = []
    for wh in [doc.get("set_warehouse")] + [it.get("warehouse") for it in (doc.get("items") or [])]:
        if wh and wh not in seen:
            seen.append(wh)
    return seen


def collect_sources(doc, settings) -> list[dict]:
    company = doc.get("company")
    src = []
    if doc.get("dgi_point_of_sale") and not cint(doc.get("dgi_pos_auto")):
        src.append({"source": "facture", "kind": "emission", "ref": doc.get("name") or "",
                    "pvf": doc.dgi_point_of_sale})
    if doc.get("pos_profile"):
        src.append({"source": "pos_profile", "kind": "emission", "ref": doc.pos_profile,
                    "pvf": frappe.db.get_value("POS Profile", doc.pos_profile, "dgi_point_of_sale") or None})
    if doc.get("branch") and _has_field("DGI Point of Sale", "branch"):
        src.append({"source": "agence", "kind": "emission", "ref": doc.branch,
                    "pvf": pvf_of_branch(company, doc.branch)})
    if cint(settings.get("use_warehouse_mapping")):
        for wh in _warehouses(doc):
            src.append({"source": "entrepot", "kind": "livraison", "ref": wh, "pvf": pvf_of_warehouse(wh)})
    return src


def describe_sources(sources) -> str:
    parts = []
    for s in sources:
        label = SOURCE_LABELS.get(s["source"], s["source"])
        ref = f" {s['ref']}" if s.get("ref") and s["source"] != "facture" else ""
        parts.append(f"{label}{ref} = {s.get('pvf') or '-'}")
    return " ; ".join(parts) if parts else _("aucune source")


def _active_pvfs(company):
    rows = frappe.get_all("DGI Point of Sale", filters={"is_active": 1}, fields=["name", "company"], order_by="name asc")
    return [r.name for r in rows if not company or not r.company or r.company == company]


# --------------------------------------------------------------------------- point de vente
def resolve(doc, settings=None) -> dict:
    """Section 4 of livrable B. Never raises: returns {"pvf", "rule", "sources", "warnings", "blocked"}."""
    settings = settings or get_settings_doc()
    company = doc.get("company")
    sources = collect_sources(doc, settings)
    out = {"pvf": None, "rule": None, "sources": sources, "warnings": [], "blocked": None}
    emission = sorted({s["pvf"] for s in sources if s["kind"] == "emission" and s.get("pvf")})
    delivery = sorted({s["pvf"] for s in sources if s["kind"] == "livraison" and s.get("pvf")})
    arbitration = (doc.get("dgi_pos_arbitration") or "").strip()

    def block(code, message):
        out["blocked"] = {"code": code, "message": message}
        out["rule"] = code
        return out

    if arbitration and doc.get("dgi_point_of_sale"):
        out["pvf"], out["rule"] = doc.dgi_point_of_sale, ARBITRAGE
        out["warnings"].append(_("Point de vente tranche par {0} : {1}").format(
            doc.get("dgi_pos_arbitrated_by") or "?", arbitration))
    elif len(emission) > 1:
        return block(CONFLIT_SOURCES, _(
            "Les sources de cette facture designent des points de vente fiscaux differents ({0}). Une facture ne "
            "peut pas etre partagee entre deux points de vente : corrigez la source erronee, ou faites trancher par "
            "un DGI Fiscal Manager (champ 'Motif d'arbitrage du point de vente').").format(describe_sources(sources)))
    elif len(emission) == 1:
        out["pvf"], out["rule"] = emission[0], EMISSION
        others = [p for p in delivery if p != emission[0]]
        if others:
            if (settings.get("warehouse_mismatch_action") or "Signaler") == "Bloquer":
                return block(EMISSION_CENTRALISEE, _(
                    "Facturation centralisee : la facture est emise pour {0} mais livree depuis un entrepot de {1} "
                    "({2}). Le parametre demande un arbitrage par un DGI Fiscal Manager.")
                    .format(emission[0], ", ".join(others), describe_sources(sources)))
            out["warnings"].append(_("{0} : emission {1}, entrepot(s) de {2}").format(
                EMISSION_CENTRALISEE, emission[0], ", ".join(others)))
    elif len(delivery) == 1:
        out["pvf"], out["rule"] = delivery[0], ENTREPOT
    elif len(delivery) > 1:
        return block(CONFLIT_ENTREPOTS, _(
            "Les entrepots de cette facture appartiennent a plusieurs points de vente fiscaux ({0}). Indiquez le point "
            "de vente sur la facture ou separez la facture.").format(describe_sources(sources)))
    else:
        dflt = settings.get("default_point_of_sale")
        dflt_company = frappe.db.get_value("DGI Point of Sale", dflt, "company") if dflt else None
        active = _active_pvfs(company)
        if dflt and frappe.db.get_value("DGI Point of Sale", dflt, "is_active") \
                and (not dflt_company or not company or dflt_company == company):
            out["pvf"], out["rule"] = dflt, DEFAUT_PARAMETRES
        elif len(active) == 1:
            out["pvf"], out["rule"] = active[0], UNIQUE_SOCIETE
        elif not active:
            out["rule"] = MODE_GLOBAL
        else:
            return block(AUCUNE_SOURCE, _(
                "Aucune source ne designe le point de vente fiscal de cette facture et la societe en a plusieurs "
                "({0}). Renseignez le POS Profile, l'agence ou le champ 'Point de vente fiscal'.")
                .format(", ".join(active)))

    if out["pvf"]:
        pvf = frappe.db.get_value("DGI Point of Sale", out["pvf"], ["company", "is_active"], as_dict=True)
        if not pvf:
            return block(PVF_INACTIF, _("Le point de vente fiscal {0} n'existe pas.").format(out["pvf"]))
        if pvf.company and company and pvf.company != company:
            return block(SOCIETE_DIFFERENTE, _("Le point de vente fiscal {0} appartient a la societe {1}, pas a {2}.")
                         .format(out["pvf"], pvf.company, company))
        if not cint(pvf.is_active):
            return block(PVF_INACTIF, _("Le point de vente fiscal {0} est inactif.").format(out["pvf"]))
    return out


def resolve_point_of_sale(doc, settings=None) -> str | None:
    """Compatibility helper (v5.2): the PVF of the invoice, or None when blocked / global mode."""
    res = resolve(doc, settings)
    return None if res["blocked"] else res["pvf"]


# --------------------------------------------------------------------------- equipment
def gateway_of(device):
    return frappe.db.get_value("DGI Gateway", {"mcf_device": device, "status": "Active"},
                               ["name", "last_seen", "kind"], as_dict=True) if device else None


def mcf_reachable(device, settings=None) -> tuple[bool, str]:
    settings = settings or get_settings_doc()
    gw = gateway_of(device)
    if not gw:
        return False, _("aucun poste MCF approuve")
    minutes = max(1, cint(settings.get("gateway_offline_minutes") or 2))
    if not gw.last_seen or get_datetime(gw.last_seen) < add_to_date(now_datetime(), minutes=-minutes):
        return False, _("poste MCF {0} sans signal depuis plus de {1} min").format(gw.name, minutes)
    return True, ""


def equipped(pvf, channel, settings=None, *, fallback=False) -> tuple[bool, str, dict]:
    """Is the channel usable for this PVF? Returns (ok, reason when not ok, equipment).
    fallback=True: the e-MCF must be the point de vente's OWN e-MCF (its token): the general token of
    the HL1 mode may designate the e-MCF of another shop, which must never receive this shop's invoices."""
    settings = settings or get_settings_doc()
    if channel == CHANNEL_EMCF:
        try:
            own = pvf.get_password("token", raise_exception=False) or ""
        except Exception:
            own = ""
        if fallback and not own:
            return False, _("pas de jeton e-MCF propre au point de vente (le jeton general ne sert pas de secours)"), {}
        if not (own or settings.get_token()):
            return False, _("aucun jeton e-MCF"), {}
        if pvf.get("token_check") == "Different":
            return False, _("le jeton du point de vente renvoie le NIM {0} au lieu de {1}").format(
                pvf.get("token_nim") or "?", pvf.get("nim") or "?"), {}
        return True, "", {"nim": pvf.get("nim") or None, "token": "point de vente" if own else "general",
                          "token_check": pvf.get("token_check") or "Non verifie"}
    device = pvf.get("mcf_device")
    if not device:
        return False, _("aucun MCF rattache"), {}
    dev = frappe.db.get_value("DGI MCF Device", device, ["status", "point_of_sale", "last_mid", "validated_mid"],
                              as_dict=True)
    if not dev:
        return False, _("MCF {0} introuvable").format(device), {}
    if dev.status != "Actif":
        return False, _("MCF {0} a l'etat {1}").format(device, dev.status), {}
    if dev.point_of_sale and dev.point_of_sale != pvf.name:
        return False, _("MCF {0} rattache a {1}").format(device, dev.point_of_sale), {}
    if not gateway_of(device):
        return False, _("aucun poste MCF approuve pour {0}").format(device), {}
    return True, "", {"nid": device, "mid": dev.validated_mid or dev.last_mid or None}


def _route_for(pvf, channel, nif, equipment):
    if channel == CHANNEL_EMCF:
        try:
            token = pvf.get_password("token", raise_exception=False) or None
        except Exception:
            token = None
        return Route(pvf.name, CHANNEL_EMCF, emcf_token=token, nif=nif or None, nim=equipment.get("nim"))
    return Route(pvf.name, CHANNEL_MCF, device=pvf.get("mcf_device"), nif=nif or None)


# --------------------------------------------------------------------------- route
def route(doc, settings=None, *, strict=True, force_channel=None) -> Route:
    """Section 5 of livrable B. Raises RoutingError (with the decision) when the invoice cannot be routed."""
    from dgi_compliance.fiscal import fallback as FB
    settings = settings or get_settings_doc()
    res = resolve(doc, settings)
    decision = {"pvf": res["pvf"], "pvf_rule": res["rule"], "sources": res["sources"],
                "warnings": list(res["warnings"]), "at": str(now_datetime())}
    if res["blocked"]:
        raise RoutingError(res["blocked"]["message"], code=res["blocked"]["code"], decision=decision)
    company = doc.get("company")
    nif = company_nif(company)
    if not res["pvf"]:                                   # MODE_GLOBAL: company without PVF (HL1 behaviour)
        prio = default_channel(settings)
        if prio == CHANNEL_MCF:
            raise RoutingError(_("Le canal prioritaire est le MCF mais la societe {0} n'a aucun point de vente fiscal "
                                 "avec un MCF.").format(company or "?"), code=AUCUN_CANAL, decision=decision)
        decision.update({"priority": prio, "priority_rule": PRIORITE_PARAMETRES, "channel": CHANNEL_EMCF,
                         "rules": [MODE_GLOBAL, PRIORITE_PARAMETRES], "equipment": {"token": "general"}})
        return Route(None, CHANNEL_EMCF, nif=nif or None, rules=[MODE_GLOBAL, PRIORITE_PARAMETRES], decision=decision)

    pvf = frappe.get_doc("DGI Point of Sale", res["pvf"])
    policy = pvf.get("fallback_policy") or POLICY_AUTO
    if pvf.get("primary_channel") in CHANNELS:
        prio, rules = pvf.primary_channel, [res["rule"], PRIORITE_POINT_DE_VENTE]
    else:
        prio, rules = default_channel(settings), [res["rule"], PRIORITE_PARAMETRES]
    channel = prio
    if pvf.get("forced_switch") and pvf.get("active_channel") in CHANNELS:
        channel = pvf.active_channel
        rules.append(f"{BASCULE_MANUELLE} {pvf.forced_switch}")
    fallback_reason = None
    if force_channel in CHANNELS:
        channel = force_channel
        rules.append(RENVOI_MANUEL)
    else:
        st = FB.state(pvf.name)
        if st and policy == POLICY_AUTO and (st.get("from") or CHANNEL_EMCF) == channel \
                and equipped(pvf, other_channel(channel), settings, fallback=True)[0]:
            fallback_reason = _("Secours {0} actif depuis {1} : {2}").format(
                other_channel(channel), st.get("since", "?"), st.get("reason") or _("canal prioritaire indisponible"))
            channel = other_channel(channel)
            rules.append(SECOURS_AUTO)

    ok, why, equipment = equipped(pvf, channel, settings)
    if not ok:
        alt = other_channel(channel)
        ok2, why2, equipment2 = equipped(pvf, alt, settings)
        if ok2 and policy != POLICY_NONE and force_channel not in CHANNELS:
            decision["warnings"].append(_("{0} : canal {1} non utilisable ({2}), canal {3} utilise").format(
                PRIORITAIRE_NON_EQUIPE, channel, why, alt))
            rules.append(PRIORITAIRE_NON_EQUIPE)
            channel, equipment = alt, equipment2
        else:
            decision.update({"priority": prio, "channel": None, "rules": rules + [AUCUN_CANAL]})
            raise RoutingError(_("Point de vente {0} : aucun canal utilisable. {1} : {2} ; {3} : {4}.").format(
                pvf.name, channel, why, alt, why2 if not ok2 else _("non autorise par la politique de secours")),
                code=AUCUN_CANAL, decision=decision)

    r = _route_for(pvf, channel, nif, equipment)
    r.fallback_reason = fallback_reason
    # MCF chosen but the station is silent: proven non-reception -> e-MCF now (symmetric fallback)
    if channel == CHANNEL_MCF and policy == POLICY_AUTO and force_channel not in CHANNELS \
            and SECOURS_AUTO not in rules:
        reachable, why_not = mcf_reachable(pvf.mcf_device, settings)
        ok_e, _why_e, eq_e = equipped(pvf, CHANNEL_EMCF, settings, fallback=True)
        if not reachable and ok_e:
            reason = _("MCF injoignable : {0}").format(why_not)
            r = _route_for(pvf, CHANNEL_EMCF, nif, eq_e)
            r.fallback_reason = reason
            r.open_fallback = {"from": CHANNEL_MCF, "to": CHANNEL_EMCF, "reason": reason}
            rules.append(SECOURS_AUTO)
            channel, equipment = CHANNEL_EMCF, eq_e
    # alternate route for a failure during the attempt (e-MCF -> MCF)
    if channel == CHANNEL_EMCF and policy == POLICY_AUTO and force_channel not in CHANNELS \
            and SECOURS_AUTO not in rules:
        ok_m, _w, eq_m = equipped(pvf, CHANNEL_MCF, settings, fallback=True)
        if ok_m:
            r.alternate = _route_for(pvf, CHANNEL_MCF, nif, eq_m)

    decision.update({"priority": prio, "priority_rule": rules[1], "channel": channel, "rules": rules,
                     "equipment": equipment, "policy": policy, "fallback_reason": r.fallback_reason})
    r.rules, r.decision = rules, decision
    if strict and channel == CHANNEL_MCF and not r.device:
        raise RoutingError(_("Le point de vente fiscal {0} est en canal MCF mais aucun MCF ne lui est rattache.")
                           .format(pvf.name), code=AUCUN_CANAL, decision=decision)
    return r


# --------------------------------------------------------------------------- explanation
def explain(decision: dict) -> str:
    if not decision:
        return ""
    parts = []
    pvf = decision.get("pvf")
    if pvf:
        parts.append(_("Point de vente {0} (regle {1} : {2}).").format(
            pvf, decision.get("pvf_rule"), describe_sources(decision.get("sources") or [])))
    else:
        parts.append(_("Aucun point de vente fiscal ({0}) : jeton general.").format(decision.get("pvf_rule")))
    if decision.get("channel"):
        origin = _("du point de vente") if decision.get("priority_rule") == PRIORITE_POINT_DE_VENTE \
            else _("des parametres")
        parts.append(_("Canal {0} ({1}) ; canal prioritaire {2} selon la configuration {3}.").format(
            decision["channel"], " > ".join(decision.get("rules") or []), decision.get("priority"), origin))
    eq = decision.get("equipment") or {}
    if eq.get("nid"):
        parts.append(_("Equipement : MCF NID {0}{1}.").format(eq["nid"], f", MID {eq['mid']}" if eq.get("mid") else ""))
    elif eq.get("nim") or eq.get("token"):
        parts.append(_("Equipement : e-MCF {0} (jeton {1}, controle : {2}).").format(
            eq.get("nim") or "-", eq.get("token") or "-", eq.get("token_check") or "-"))
    if decision.get("fallback_reason"):
        parts.append(_("Secours : {0}.").format(decision["fallback_reason"]))
    for w in decision.get("warnings") or []:
        parts.append(_("Avertissement : {0}.").format(w))
    return " ".join(parts)


def decision_fields(route_or_decision) -> dict:
    """Values for the routing fields of DGI Fiscal Operation."""
    decision = route_or_decision.decision if isinstance(route_or_decision, Route) else (route_or_decision or {})
    rules = decision.get("rules") or [decision.get("pvf_rule")]
    return {"routing_rule": " > ".join([r for r in rules if r])[:140] or None,
            "routing_explanation": explain(decision)[:2000] or None,
            "routing_sources": json.dumps(decision, ensure_ascii=False, default=str, indent=1)}


@frappe.whitelist()
def preview_route(invoice: str) -> dict:
    """Desk: what ERPNext would decide for this invoice now (no side effect)."""
    doc = frappe.get_doc("Sales Invoice", invoice)
    doc.check_permission("read")
    try:
        r = route(doc, strict=False)
        return {"ok": True, "explanation": r.explanation(), "channel": r.channel, "point_of_sale": r.point_of_sale}
    except RoutingError as e:
        return {"ok": False, "code": e.code, "message": str(e), "explanation": explain(e.decision)}
