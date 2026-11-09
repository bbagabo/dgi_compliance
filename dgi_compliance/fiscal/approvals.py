"""Separation of duties (v5.4.0): who may initiate, who may approve, and the two-person rule.

Three sensitive actions need a second person (four eyes), each with its own setting:

  * validation of a proposed MCF (DGI MCF Device)        - setting `station_approval_two_people`
  * approval of an enrolled gateway / Poste MCF (key)    - setting `station_approval_two_people`
  * approval (submission) of a manual channel switch     - setting `switch_requires_two_people`

Up to v5.3.2 only a DGI Fiscal Manager (or System Manager) could approve. A company with two users
(a manager and a cashier) was stuck as soon as the manager initiated the action himself: a second
DGI Fiscal Manager was needed. v5.4.0 adds the restricted role DGI Approbateur: it approves or refuses
what somebody else initiated, and nothing more (no enrolment code, no settings, no point de vente
configuration, no arbitration). With the cashier holding DGI Caisse MCF + DGI Approbateur, two people
cover every case, whichever of them initiates.

Rules enforced here for every approval:
  * the approver is a HUMAN desk user (System User, enabled); never Guest, never a technical account of
    a gateway (role DGI Gateway, ...@dgi-gateway.invalid) or of the v4 MCF agent (role DGI MCF Agent);
  * the approver is not the initiator while the two-person rule is on;
  * when the rule is off (one-person company), a self-approval is accepted but ALWAYS recorded as an
    AUTO_APPROBATION alert in the DGI Gateway Event journal.
"""
import frappe
from frappe import _
from frappe.utils import cint

APPROVER_ROLE = "DGI Approbateur"
MANAGER_ROLES = ("System Manager", "DGI Fiscal Manager")
APPROVER_ROLES = MANAGER_ROLES + (APPROVER_ROLE,)
STATION_ROLES = ("DGI Caisse MCF",) + MANAGER_ROLES
TECHNICAL_ROLES = ("DGI Gateway", "DGI MCF Agent")
TECHNICAL_DOMAIN = "@dgi-gateway.invalid"

RULE_STATION = "station_approval_two_people"
RULE_SWITCH = "switch_requires_two_people"


def _settings():
    from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings
    return get_settings()


def rule_on(setting: str) -> bool:
    """Two-person rule in force (an empty value means ON: secure by default)."""
    value = _settings().get(setting)
    return bool(cint(1 if value in (None, "") else value))


def is_technical_account(user: str | None) -> bool:
    """Accounts that act for a machine: never an approver, never counted as a person."""
    if not user or user == "Guest":
        return True
    if user.lower().endswith(TECHNICAL_DOMAIN):
        return True
    if user == "Administrator":
        return False                    # frappe.get_roles() gives every role to Administrator: not a machine
    row = frappe.db.get_value("User", user, ["user_type", "enabled"], as_dict=True)
    if not row or not cint(row.enabled) or (row.user_type or "System User") != "System User":
        return True
    return bool(frappe.db.exists("Has Role", {"parent": user, "parenttype": "User",
                                              "role": ["in", list(TECHNICAL_ROLES)]}))


def is_manager(user: str | None = None) -> bool:
    return bool(set(frappe.get_roles(user or frappe.session.user)) & set(MANAGER_ROLES))


def can_approve(user: str | None = None) -> bool:
    user = user or frappe.session.user
    return not is_technical_account(user) and bool(set(frappe.get_roles(user)) & set(APPROVER_ROLES))


def require_approver(action: str) -> str:
    """Raise unless the current user may approve. Returns the role that grants the approval."""
    user = frappe.session.user
    if is_technical_account(user):
        frappe.throw(_("{0} : un compte technique (passerelle, agent MCF) ne peut jamais approuver. "
                       "L'approbation est faite par une personne.").format(action), frappe.PermissionError)
    roles = set(frappe.get_roles(user))
    for role in APPROVER_ROLES:
        if role in roles:
            return role
    frappe.throw(_("{0} : role DGI Fiscal Manager ou DGI Approbateur requis.").format(action), frappe.PermissionError)


def check_two_people(initiator: str | None, setting: str, action: str, *, reference: str | None = None,
                     point_of_sale: str | None = None, device: str | None = None, gateway: str | None = None) -> str:
    """Four-eyes check. Returns 'other' (approver differs from the initiator), 'self' (rule off: logged)
    or 'none' (no recorded initiator, e.g. a service gateway enrolled with its code only)."""
    user = frappe.session.user
    if not initiator:
        return "none"
    if initiator != user:
        return "other"
    if rule_on(setting):
        # ValidationError (as before v5.4.0): a business rule, the user may hold the right role
        frappe.throw(_("Regle des deux personnes : {0} a ete initie par vous ; une autre personne (DGI Fiscal "
                       "Manager ou DGI Approbateur) doit l'approuver.").format(action))
    from dgi_compliance.gateway.events import log_event
    log_event(gateway, "AUTO_APPROBATION",
              _("{0} : approuve par la personne qui l'a initie ({1}), regle des deux personnes desactivee ({2}).")
              .format(action, user, setting), level="Alerte", point_of_sale=point_of_sale, device=device,
              details={"reference": reference, "setting": setting, "user": user})
    return "self"


def approval_note(role: str, mode: str) -> str:
    note = _("role {0}").format(role)
    if mode == "self":
        note += _(", auto-approbation (regle des deux personnes desactivee)")
    return note


# --------------------------------------------------------------------------- readiness report
def _people_with(roles) -> list[str]:
    users = frappe.get_all("Has Role", filters={"role": ["in", list(roles)], "parenttype": "User"},
                           pluck="parent", distinct=True)
    return sorted(u for u in set(users) if not is_technical_account(u))


def _satisfiable(initiators, approvers) -> bool:
    return any(a != i for i in initiators for a in approvers)


@frappe.whitelist()
def separation_report() -> dict:
    """Who can initiate and who can approve each four-eyes action, and whether two distinct persons exist.
    Technical accounts (gateways, MCF agent) are listed apart: they are never counted as persons."""
    frappe.only_for(APPROVER_ROLES + ("Accounts Manager",))
    approvers = _people_with(APPROVER_ROLES)
    controls = [
        {"action": _("Validation d'un MCF propose"), "setting": RULE_STATION,
         "initiators": _people_with(STATION_ROLES)},
        {"action": _("Approbation d'un Poste MCF (enrolement)"), "setting": RULE_STATION,
         "initiators": _people_with(STATION_ROLES)},
        {"action": _("Bascule manuelle de canal"), "setting": RULE_SWITCH,
         "initiators": _people_with(MANAGER_ROLES)},
    ]
    for c in controls:
        c["rule_on"] = rule_on(c["setting"])
        c["approvers"] = approvers
        c["ok"] = (not c["rule_on"] and bool(approvers)) or _satisfiable(c["initiators"], approvers)
    technical = sorted(set(frappe.get_all("Has Role", filters={"role": ["in", list(TECHNICAL_ROLES)],
                                                               "parenttype": "User"}, pluck="parent")))
    persons = sorted(set(approvers) | {u for c in controls for u in c["initiators"]})
    return {"controls": controls, "persons": persons, "technical_accounts": technical,
            "ok": all(c["ok"] for c in controls)}
