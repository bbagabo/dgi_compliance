"""Manual switch of the normalization channel of ONE point de vente fiscal (never global, never per invoice).

v5.3.0: a submitted switch is 'in force' (DGI Point of Sale.forced_switch) and imposes its channel over
the prioritized one until a switch 'Retour au canal principal' (= return to the prioritized channel).

Submitted by a DGI Fiscal Manager. Refused while an operation of the PVF is Leased, Uncertain,
Checking or in Dispute. Queued operations (never sent to a device) are cancelled at the switch;
their invoices go back to 'Pending' and are normalized again on the new channel.
Since v5.2 (H1/H2 confirmed: a PVF may hold an e-MCF and an MCF and switch), a DGI reference is
only required when the setting `switch_requires_dgi_reference` is ticked. The PVF must still allow
manual switches (`switch_allowed`). The automatic per-invoice fallback is in fiscal.fallback.
"""
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

from dgi_compliance.fiscal.router import CHANNEL_EMCF, CHANNEL_MCF

FALLBACK_REASONS = ("Panne e-MCF", "Panne MCF", "Maintenance")
RETURN_REASON = "Retour au canal principal"        # = return to the prioritized channel (v5.3.0)
IN_FLIGHT = ("Leased", "Uncertain", "Checking", "Dispute")


REQUEST_FIELDS = ("point_of_sale", "to_channel", "reason", "dgi_reference", "notes")


class DGIChannelSwitch(Document):
    def validate(self):
        self._freeze_request_for_approvers()
        self.from_channel = frappe.db.get_value("DGI Point of Sale", self.point_of_sale, "active_channel") or ""
        if self.reason == RETURN_REASON:
            # v5.3.0: the return goes to the prioritized channel (PVF override or general setting)
            from dgi_compliance.fiscal.router import default_channel
            override = frappe.db.get_value("DGI Point of Sale", self.point_of_sale, "primary_channel")
            self.to_channel = override or default_channel()
            if not frappe.db.get_value("DGI Point of Sale", self.point_of_sale, "forced_switch"):
                frappe.throw(_("Aucune bascule manuelle en vigueur sur ce point de vente : il suit deja son canal prioritaire."))
            return self._requested_by()
        self._requested_by()
        if self.to_channel == self.from_channel:
            frappe.throw(_("Le point de vente est deja sur le canal {0}.").format(self.to_channel))

    def _freeze_request_for_approvers(self):
        """v5.4.0: a DGI Approbateur approves the request as it was made; he never changes it (otherwise he would
        approve his own version). Frappe 16 needs the 'write' right to submit, hence this guard."""
        if self.is_new() or frappe.flags.in_patch:
            return
        from dgi_compliance.fiscal.approvals import is_manager
        if is_manager():
            return
        changed = [f for f in REQUEST_FIELDS if self.has_value_changed(f)]
        if changed:
            frappe.throw(_("Un DGI Approbateur approuve ou laisse la demande en l'etat ; il ne la modifie pas ({0}).")
                         .format(", ".join(self.meta.get_label(f) for f in changed)), frappe.PermissionError)

    def _requested_by(self):
        if not self.requested_by:
            self.requested_by = frappe.session.user

    def before_submit(self):
        # v5.4.0: submitting = approving. A DGI Fiscal Manager or a DGI Approbateur, never a technical account,
        # and another person than the requester while the two-person rule is on (fiscal.approvals).
        from dgi_compliance.fiscal import approvals as A
        role = A.require_approver(_("Approbation de la bascule de canal"))
        from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings
        settings = get_settings()
        pvf = frappe.get_doc("DGI Point of Sale", self.point_of_sale)
        checks = []
        if self.reason in FALLBACK_REASONS:
            if not pvf.get("switch_allowed"):
                frappe.throw(_("Bascule manuelle non autorisee pour {0} (case 'Bascule manuelle autorisee').")
                             .format(pvf.name))
            ref = self.dgi_reference or pvf.get("dgi_switch_reference")
            if cint(settings.get("switch_requires_dgi_reference")) and not ref:
                frappe.throw(_("Indiquez la reference de l'accord DGI."))
            checks.append(_("bascule autorisee") + (_(" (reference {0})").format(ref) if ref else ""))
        # v5.4.1: 'Mise en service' is exempt from the two-person rule ONLY while it is a real commissioning:
        # the first switch of a point de vente that never normalized an invoice. Before, this reason let one
        # person switch any point de vente alone, whatever the two-person rule said. Any other use of it is
        # approved like every switch.
        commissioning = self.reason == "Mise en service" and not frappe.db.exists(
            "DGI Channel Switch", {"point_of_sale": self.point_of_sale, "docstatus": 1, "name": ["!=", self.name]}) \
            and not frappe.db.exists("Sales Invoice", {"dgi_point_of_sale": self.point_of_sale,
                                                       "custom_dgi_status": "Normalized"})
        if commissioning:
            mode = "other" if self.owner != frappe.session.user else "self"
        else:
            mode = A.check_two_people(self.owner, A.RULE_SWITCH, _("la bascule {0} de {1}").format(
                self.name, self.point_of_sale), reference=self.name, point_of_sale=self.point_of_sale)
        self.flags.dgi_approval = {"role": role, "mode": mode}
        busy = frappe.get_all("DGI Fiscal Operation", filters={"point_of_sale": pvf.name, "status": ["in", list(IN_FLIGHT)]},
                              pluck="name", limit=5)
        if busy:
            frappe.throw(_("Operations en cours sur ce point de vente ({0}) : attendez leur fin ou leur "
                           "rapprochement avant de basculer.").format(", ".join(busy)))
        checks.append(_("aucune operation en cours"))
        if self.to_channel == CHANNEL_MCF:
            if not pvf.get("mcf_device"):
                frappe.throw(_("Aucun MCF rattache a ce point de vente."))
            if frappe.db.get_value("DGI MCF Device", pvf.mcf_device, "status") != "Actif":
                frappe.throw(_("Le MCF {0} n'est pas actif.").format(pvf.mcf_device))
            minutes = max(1, cint(settings.get("gateway_offline_minutes") or 2))
            gw = frappe.db.get_value("DGI Gateway", {"mcf_device": pvf.mcf_device, "status": "Active"},
                                     ["name", "last_seen"], as_dict=True)
            if not gw:
                frappe.throw(_("Aucune passerelle active pour le MCF {0}.").format(pvf.mcf_device))
            if not gw.last_seen or get_datetime(gw.last_seen) < add_to_date(now_datetime(), minutes=-minutes):
                frappe.throw(_("La passerelle {0} ne repond pas depuis plus de {1} min.").format(gw.name, minutes))
            checks.append(_("passerelle {0} en ligne, MCF {1} actif").format(gw.name, pvf.mcf_device))
        elif self.to_channel == CHANNEL_EMCF:
            if not (pvf.get("nim") or pvf.get_password("token", raise_exception=False) or settings.get_token()):
                frappe.throw(_("Aucun e-MCF (NIM ou jeton) configure pour ce point de vente."))
            checks.append(_("e-MCF configure"))
        self.checks = "; ".join(checks)
        self.approved_by = frappe.session.user

    def on_submit(self):
        from dgi_compliance.gateway.events import log_event
        cancelled = 0
        for name in frappe.get_all("DGI Fiscal Operation", filters={"point_of_sale": self.point_of_sale, "status": "Queued"},
                                   pluck="name"):
            from dgi_compliance.fiscal.operations import withdraw_queued
            op = withdraw_queued(name, _("Annulee par la bascule {0}").format(self.name))    # under row lock (v5.4.1)
            if not op:
                frappe.throw(_("L'operation {0} vient d'etre prise par le poste MCF : attendez sa fin avant de "
                               "basculer.").format(name))
            if op.sales_invoice:
                frappe.db.set_value("Sales Invoice", op.sales_invoice,
                                    {"custom_dgi_status": "Pending",
                                     "custom_dgi_error": _("Bascule de canal {0} : relancez la normalisation.").format(self.name)},
                                    update_modified=False)
            cancelled += 1
        pvf = frappe.get_doc("DGI Point of Sale", self.point_of_sale)
        pvf.active_channel = self.to_channel
        pvf.forced_switch = None if self.reason == RETURN_REASON else self.name
        pvf.flags.dgi_channel_switch = True
        pvf.flags.dgi_system_update = True
        pvf.save(ignore_permissions=True)
        self.db_set({"effective_on": now_datetime(), "cancelled_operations": cancelled})
        approval = self.flags.get("dgi_approval") or {}
        log_event(None, "BASCULE_CANAL", _("{0} : {1} -> {2} ({3}), {4} operation(s) en file annulee(s) ; demandee par "
                                           "{5}, approuvee par {6} ({7}).")
                  .format(self.point_of_sale, self.from_channel, self.to_channel, self.reason, cancelled,
                          self.owner, self.approved_by, approval.get("role") or "-"),
                  level="Alerte" if self.reason in FALLBACK_REASONS else "Info", point_of_sale=self.point_of_sale,
                  details={"switch": self.name, "requested_by": self.owner, "approved_by": self.approved_by,
                           "role": approval.get("role"), "mode": approval.get("mode")})

    def before_cancel(self):
        frappe.throw(_("Une bascule ne s'annule pas : creez une bascule inverse."))
