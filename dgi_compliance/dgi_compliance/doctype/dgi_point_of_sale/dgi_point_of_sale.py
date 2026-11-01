import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, now_datetime

IN_FLIGHT = ("Queued", "Leased", "Uncertain", "Checking", "Dispute", "Pending")
FISCAL_ROLES = ("System Manager", "DGI Fiscal Manager")
RESTRICTED = ("primary_channel", "priority_reason", "fallback_policy", "branch", "mcf_device", "nim", "company")


class DGIPointofSale(Document):
    """Point de vente fiscal (PVF): pivot of the fiscal routing (v5.3.0, livrable B section 3).

    * NIM (e-MCF, `nim`) and NID (MCF, `mcf_device`) are kept apart, never merged; the token is
      checked against the NIM (`check_emcf_token`).
    * `primary_channel` is an OVERRIDE of the setting `primary_channel` (blank = follow it); it needs a
      reason and a DGI Fiscal Manager / System Manager.
    * `active_channel` is computed: the prioritized channel, or the channel imposed by a manual switch in
      force (`forced_switch`, DGI Channel Switch). The automatic fallback is shown apart.
    Matrix A (v3): when 'Sales Location' is enabled, NID and token requirements are enforced on save."""

    def validate(self):
        self._check_rights()
        self._validate_company()
        self._validate_identifiers()
        self._validate_priority()
        self._validate_mcf_device()
        self._validate_matrix_a()

    # ---- v5.3.0 ----
    def _check_rights(self):
        if frappe.flags.in_patch or frappe.flags.in_install or self.flags.get("dgi_system_update"):
            return
        changed = [f for f in RESTRICTED if (self.is_new() and self.get(f)) or
                   (not self.is_new() and self.has_value_changed(f))]
        if changed and not (set(frappe.get_roles()) & set(FISCAL_ROLES)):
            frappe.throw(_("Seul un DGI Fiscal Manager ou un System Manager peut modifier : {0}.")
                         .format(", ".join(self.meta.get_label(f) for f in changed)), frappe.PermissionError)

    def _validate_company(self):
        if not self.company and self.source != "API" and frappe.db.count("Company") > 1:
            frappe.throw(_("Plusieurs societes existent : indiquez la societe de ce point de vente fiscal."))
        if self.branch and cint(self.is_active):
            for other in frappe.get_all("DGI Point of Sale", filters={"branch": self.branch, "is_active": 1,
                                                                      "name": ["!=", self.name]},
                                        fields=["name", "company"]):
                if not other.company or not self.company or other.company == self.company:
                    frappe.throw(_("L'agence {0} est deja rattachee au point de vente fiscal {1} de la meme societe.")
                                 .format(self.branch, other.name))

    def _validate_identifiers(self):
        if self.nim and cint(self.is_active):
            other = frappe.db.get_value("DGI Point of Sale", {"nim": self.nim, "is_active": 1, "name": ["!=", self.name]})
            if other:
                frappe.throw(_("Le NIM {0} est deja rattache au point de vente fiscal actif {1}.").format(self.nim, other))
        if not self.is_new() and (self.has_value_changed("nim") or self.has_value_changed("token")):
            self.token_check, self.token_nim, self.token_checked_on = "Non verifie", None, None

    def _validate_priority(self):
        from dgi_compliance.fiscal.router import CHANNELS, default_channel
        if self.primary_channel and not (self.priority_reason or "").strip():
            frappe.throw(_("Indiquez le motif de la derogation au canal prioritaire general."))
        if not self.primary_channel:
            self.priority_reason = None
        if not self.is_new() and self.has_value_changed("active_channel") and not self.flags.get("dgi_channel_switch"):
            frappe.throw(_("Le canal en vigueur ne se modifie pas a la main : il suit le canal prioritaire, ou une "
                           "bascule manuelle (DGI Channel Switch, tracee et approuvee)."))
        if self.forced_switch and self.active_channel in CHANNELS:
            return
        self.forced_switch = None
        self.active_channel = self.primary_channel or default_channel()

    def _validate_mcf_device(self):
        if not self.mcf_device:
            return
        dev_pos = frappe.db.get_value("DGI MCF Device", self.mcf_device, "point_of_sale")
        if dev_pos and dev_pos != self.name:
            frappe.throw(_("Le MCF {0} est rattache au point de vente {1}.").format(self.mcf_device, dev_pos))
        if not self.is_new() and self.has_value_changed("mcf_device"):
            old = self.get_doc_before_save().mcf_device if self.get_doc_before_save() else None
            if old and frappe.db.exists("DGI Fiscal Operation", {"mcf_device": old, "point_of_sale": self.name,
                                                                "status": ["in", list(IN_FLIGHT)]}):
                frappe.throw(_("Des operations sont en cours sur le MCF {0} : terminez-les avant de le remplacer.").format(old))

    def on_update(self):
        if self.mcf_device and frappe.db.get_value("DGI MCF Device", self.mcf_device, "point_of_sale") != self.name:
            frappe.db.set_value("DGI MCF Device", self.mcf_device, "point_of_sale", self.name)
        if self.nim and frappe.db.exists("DGI EMCF", self.nim) \
                and frappe.db.get_value("DGI EMCF", self.nim, "point_of_sale") != self.name:
            frappe.db.set_value("DGI EMCF", self.nim, "point_of_sale", self.name, update_modified=False)
        before = self.get_doc_before_save()
        if self.mcf_device and (not before or before.mcf_device != self.mcf_device):
            for gw in frappe.get_all("DGI Gateway", filters={"point_of_sale": self.name,
                                                             "status": ["in", ["Nouvelle", "Code emis"]]}, pluck="name"):
                frappe.db.set_value("DGI Gateway", gw, "mcf_device", self.mcf_device, update_modified=False)
        if before and (before.primary_channel or "") != (self.primary_channel or ""):
            from dgi_compliance.gateway.events import log_event
            log_event(None, "PRIORITE_MODIFIEE", _("{0} : canal prioritaire {1} -> {2} par {3} ({4}).").format(
                self.name, before.primary_channel or _("parametres generaux"),
                self.primary_channel or _("parametres generaux"), frappe.session.user, self.priority_reason or "-"),
                level="Alerte", point_of_sale=self.name)

    # ---- Matrix A (v3) ----
    def _validate_matrix_a(self):
        mcf_only = self.active_channel == "MCF" and not self.nim \
            and not self.get_password("token", raise_exception=False)
        if self.is_sales_location:
            if not self.nid_requirement:
                self.nid_requirement = "Mandatory"
            if not self.token_requirement:
                self.token_requirement = "Optional" if mcf_only else "Mandatory"
        else:
            self.nid_requirement = self.nid_requirement or "Optional"
            self.token_requirement = self.token_requirement or "Optional"
        if (self.nid_requirement == "Mandatory") and not (self.nid or "").strip():
            if self.mcf_device and self.active_channel == "MCF":
                self.nid = self.mcf_device
            else:
                frappe.throw(_("Identifiant imprime obligatoire pour un point de vente (Sales Location). Matrice A."))
        if (self.token_requirement == "Mandatory") and not mcf_only and not (
                self.get_password("token", raise_exception=False) or self._global_token()):
            frappe.throw(_("Token obligatoire pour ce point de vente (Sales Location). "
                           "Renseignez un token ici ou un token global dans DGI Compliance Settings. Matrice A."))

    @staticmethod
    def _global_token():
        try:
            from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings
            return get_settings().get_token()
        except Exception:
            return ""


def refresh_active_channels():
    """After a change of the general priority: every PVF without manual switch follows it."""
    from dgi_compliance.fiscal.router import default_channel
    prio = default_channel()
    for row in frappe.get_all("DGI Point of Sale", fields=["name", "primary_channel", "forced_switch", "active_channel"]):
        if row.forced_switch:
            continue
        target = row.primary_channel or prio
        if row.active_channel != target:
            frappe.db.set_value("DGI Point of Sale", row.name, "active_channel", target, update_modified=False)
    frappe.clear_document_cache("DGI Point of Sale")


@frappe.whitelist(methods=["POST"])
def check_emcf_token(point_of_sale: str) -> dict:
    """'Tester le jeton e-MCF' (no fiscal effect): GET /api/info/status with the token of this point de
    vente; the NIM returned for the token must be the NIM of the point de vente."""
    from dgi_compliance.edef import client
    from dgi_compliance.gateway.events import log_event
    frappe.only_for(FISCAL_ROLES)
    pvf = frappe.get_doc("DGI Point of Sale", point_of_sale)
    try:
        frappe.local.dgi_emcf_token = pvf.get_password("token", raise_exception=False) or None
    except Exception:
        frappe.local.dgi_emcf_token = None
    try:
        res = client.call("GET", "/api/info/status", retries=0)
    finally:
        frappe.local.dgi_emcf_token = None
    data = res.get("data") if isinstance(res.get("data"), dict) else {}
    nim = (data.get("nim") or "").strip()
    if not res.get("ok") or not nim:
        result, message = "Erreur", _("Pas de reponse exploitable de l'e-MCF (HTTP {0}).").format(res.get("status") or 0)
    elif pvf.nim and nim != pvf.nim:
        result, message = "Different", _("Le jeton designe l'e-MCF {0}, le point de vente est rattache a {1}.").format(nim, pvf.nim)
    else:
        result, message = "OK", _("Le jeton designe l'e-MCF {0}.").format(nim)
    frappe.db.set_value("DGI Point of Sale", pvf.name, {"token_check": result, "token_nim": nim or None,
                                                       "token_checked_on": now_datetime()}, update_modified=False)
    frappe.clear_document_cache("DGI Point of Sale", pvf.name)
    log_event(None, "NIM_JETON_DIFFERENT" if result == "Different" else "TEST_CONNECTIVITE",
              _("{0} : test du jeton e-MCF : {1}").format(pvf.name, message),
              level="Critique" if result == "Different" else "Info", point_of_sale=pvf.name,
              details={"status": res.get("status"), "nim": nim, "expected": pvf.nim})
    return {"result": result, "nim": nim, "message": message}
