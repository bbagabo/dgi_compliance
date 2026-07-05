import frappe
from frappe import _
from frappe.model.document import Document


class DGIPointofSale(Document):
    """Point of sale / e-MCF binding. Holds NID + (optional) per-POS token, and the
    NID/Token requirement (Matrix A). When 'Sales Location' is enabled, NID and Token are
    enforced as Mandatory and validated on save."""

    def validate(self):
        # Matrix A default: a sales location requires both NID and Token.
        if self.is_sales_location:
            if not self.nid_requirement:
                self.nid_requirement = "Mandatory"
            if not self.token_requirement:
                self.token_requirement = "Mandatory"
        else:
            self.nid_requirement = self.nid_requirement or "Optional"
            self.token_requirement = self.token_requirement or "Optional"

        # Enforce Matrix A on the record itself so a sales location is never half-configured.
        if (self.nid_requirement == "Mandatory") and not (self.nid or "").strip():
            frappe.throw(_("NID obligatoire pour un point de vente (Sales Location). Matrice A."))
        if (self.token_requirement == "Mandatory") and not (self.get_password("token", raise_exception=False) or self._global_token()):
            frappe.throw(_("Token obligatoire pour ce point de vente (Sales Location). "
                           "Renseignez un token ici ou un token global dans DGI Compliance Settings. Matrice A."))

    @staticmethod
    def _global_token():
        try:
            from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings
            return get_settings().get_token()
        except Exception:
            return ""
