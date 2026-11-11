import frappe
from frappe import _
from frappe.model.document import Document

IN_FLIGHT = ("Queued", "Leased", "Uncertain", "Checking", "Dispute")
# v5.4.1: values read in the MCF, results of the validation and of the tests. Written by the SFE only
# (fiscal.registry, fiscal.operations, fiscal.mcf_status): 'read only' on the form does not protect the API.
SYSTEM_FIELDS = ("nif", "last_mid", "taxpayer_name", "taxpayer_address", "observed_on", "discovered_by",
                 "discovered_on", "discovered_gateway", "validated_by", "validated_on", "validated_mid", "nif_check",
                 "accepted_differences", "last_tc", "tax_rates", "last_invoice", "last_status", "last_status_on",
                 "status_flags", "dgi_last_sync", "dgi_pending", "sync_alert", "connection_status",
                 "last_connectivity_test", "last_connectivity_result", "last_test_by", "last_test_origin",
                 "test_dgi_state", "test_dgi_last_sync", "test_dgi_unsent", "test_clock_offset_s",
                 "connection_test_details", "company")


class DGIMCFDevice(Document):
    """Physical MCF, key = NID (serial number). v5.3.0: only an 'Actif' MCF receives invoices, and it
    becomes Actif only through fiscal.registry.validate_device (explicit validation of values read in
    the MCF). A MID or NIF read later that differs from the validation sets it to 'A revalider'."""

    def validate(self):
        self.nid = (self.nid or "").strip().upper()
        if not self.nid or any(c.isspace() for c in self.nid) or "-" in self.nid[-2:]:
            frappe.throw(_("NID invalide : saisissez le numero de serie (partie NID du MID, sans le compteur d'activation)."))
        self._guard_system_fields()
        self._guard_activation()
        if self.status == "Actif" and not self.point_of_sale:
            frappe.throw(_("Un MCF actif doit etre rattache a un point de vente fiscal."))
        if self.point_of_sale:
            other = frappe.db.get_value("DGI MCF Device", {"point_of_sale": self.point_of_sale, "status": "Actif",
                                                           "name": ["!=", self.name]}, "name")
            if other and self.status == "Actif":
                frappe.throw(_("Le point de vente {0} a deja un MCF actif ({1}).").format(self.point_of_sale, other))
        if not self.is_new() and self.has_value_changed("point_of_sale") and frappe.db.exists(
                "DGI Fiscal Operation", {"mcf_device": self.name, "status": ["in", list(IN_FLIGHT)]}):
            frappe.throw(_("Des operations sont en cours sur ce MCF : terminez-les avant de changer son rattachement."))

    def _guard_system_fields(self):
        if self.flags.get("dgi_registration") or frappe.flags.in_patch or frappe.flags.in_install \
                or frappe.flags.in_migrate or frappe.flags.get("dgi_allow_direct_activation"):
            return
        fields = [f for f in SYSTEM_FIELDS if self.meta.has_field(f)]
        if self.is_new():
            # declared by hand: never read, never validated ('Declare'); the values come from the MCF itself
            for f in fields:
                self.set(f, None)
            return
        before = self.get_doc_before_save()
        if not before:
            return
        for f in fields:
            self.set(f, before.get(f))

    def _guard_activation(self):
        if self.status != "Actif" or self.flags.get("dgi_registration") or frappe.flags.in_patch \
                or frappe.flags.in_install or frappe.flags.get("dgi_allow_direct_activation"):
            return
        before = None if self.is_new() else self.get_doc_before_save()
        if before and before.status == "Actif":
            return
        frappe.throw(_("Un MCF passe a l'etat Actif seulement par la validation d'un responsable (bouton 'Valider ce "
                       "MCF'), apres lecture de ses valeurs depuis un Poste MCF."))

    def on_trash(self):
        if frappe.db.exists("DGI Fiscal Operation", {"mcf_device": self.name}):
            frappe.throw(_("Un MCF qui a normalise des factures ne se supprime pas : passez-le en 'Retire'."))
