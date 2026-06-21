"""Configurable validation / locking engine for DGI compliance.

Every rule lives in a user-editable DocType, so nothing here is hard-coded:

  * Matrix A  -> DGI Point of Sale     (NID / Token requirement per sales location)
  * Matrix B  -> DGI Customer Type     (mandatory customer-card fields per client type)
  * Matrix C  -> DGI Validation Matrix (Invoice Type x Country)
  * Matrix D  -> DGI Validation Matrix (Item Type x Tax Group)
  * Matrix E  -> DGI Validation Matrix (Prepayment/Invoice Type x Item Type)
  * Matrix F  -> DGI Validation Matrix (Credit Nature x Item Type)
  * Matrix G  -> DGI Customer Type Mapping (native ERPNext Customer Type -> allowed DGI type)
  * Invoice-type context rules (FV/EV default, FT/ET prepayment, FA/EA credit notes)

The engine is wired into the Sales Invoice `validate` event. It blocks invalid combinations
(unless an authorised user ticks the override) and always records what happened in the audit log.
It never touches ERPNext core DocTypes. The LOC/FOR VAT group has been removed in v3.0.
"""
import frappe
from frappe import _
from frappe.utils import flt

from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings
from dgi_compliance.dgi_compliance.doctype.dgi_validation_matrix.dgi_validation_matrix import (
    MATRIX_C, MATRIX_D, MATRIX_E, MATRIX_F,
)

WILDCARDS = {None, "", "Any", "All"}

CREDIT_TYPES = ("FA", "EA")
PREPAYMENT_TYPES = ("FT", "ET")
STANDARD_TYPES = ("FV", "EV")
EXPORT_TYPES = ("EV", "ET", "EA")


# --------------------------------------------------------------------------- #
# Rule lookup
# --------------------------------------------------------------------------- #

def _rows(matrix_type):
    """Active rows for a matrix type (request-cached)."""
    cache = frappe.flags.setdefault("_dgi_matrix_cache", {})
    if matrix_type not in cache:
        cache[matrix_type] = frappe.get_all(
            "DGI Validation Matrix",
            filters={"matrix_type": matrix_type, "is_active": 1},
            fields=["name", "status", "enforcement", "priority", "message",
                    "invoice_type", "country_scope",
                    "item_type", "tax_group", "credit_nature"],
        )
    return cache[matrix_type]


def _cell_match(rule_value, input_value):
    return rule_value in WILDCARDS or str(rule_value) == str(input_value)


def _best_rule(matrix_type, dims):
    """Return the most specific, highest-priority active rule that matches `dims`.

    `dims` maps a DGI Validation Matrix fieldname -> the resolved input value.
    Specificity = number of non-wildcard dimensions that explicitly match the input."""
    best = None
    best_key = None
    for r in _rows(matrix_type):
        if not all(_cell_match(r.get(f), v) for f, v in dims.items()):
            continue
        specificity = sum(1 for f in dims if r.get(f) not in WILDCARDS)
        key = (flt(r.get("priority")), specificity)
        if best is None or key > best_key:
            best, best_key = r, key
    return best


def _decide(matrix_type, dims, label):
    """Evaluate one matrix. Returns None when allowed, or a violation dict when blocked/warned."""
    rule = _best_rule(matrix_type, dims)
    if not rule or (rule.get("status") or "Allow") == "Allow":
        return None
    dim_txt = ", ".join(f"{k}={v}" for k, v in dims.items() if v not in (None, ""))
    msg = rule.get("message") or _("{0}: combinaison interdite ({1}).").format(label, dim_txt)
    return {"matrix": label, "enforcement": rule.get("enforcement") or "Block",
            "message": msg, "rule": rule.get("name")}


# --------------------------------------------------------------------------- #
# Input resolvers (standard ERPNext fields only)
# --------------------------------------------------------------------------- #

def resolve_invoice_type(doc, settings=None):
    settings = settings or get_settings()
    it = doc.get("dgi_invoice_type")
    if it:
        return it
    from dgi_compliance.edef.mapper import _invoice_type
    return _invoice_type(doc, settings)


def _country_iso2(country_name):
    if not country_name:
        return None
    code = frappe.db.get_value("Country", country_name, "code")
    return (code or "").upper() or None


def resolve_country_scope(doc, settings=None):
    settings = settings or get_settings()
    local = (getattr(settings, "country_code_local", None) or "CD").upper()
    country = None
    if doc.get("customer_address"):
        country = frappe.db.get_value("Address", doc.customer_address, "country")
    if not country and doc.get("company"):
        country = frappe.db.get_value("Company", doc.company, "country")
    iso2 = _country_iso2(country)
    # If we cannot resolve a country, assume local (do not block on missing data).
    if iso2 is None:
        return "CD" if local == "CD" else local
    return "CD" if iso2 == local else "Non-CD"


def resolve_item_type(item_code):
    if not item_code:
        return "BIE"
    explicit = frappe.db.get_value("Item", item_code, "dgi_item_type")
    if explicit:
        return explicit
    is_stock = frappe.db.get_value("Item", item_code, "is_stock_item")
    return "SER" if is_stock == 0 else "BIE"


def resolve_tax_group(doc_item, settings):
    return settings.tax_group_for(doc_item.get("item_tax_template"), None)


def resolve_credit_nature(doc):
    return (doc.get("custom_dgi_reference_type") or "").upper() or None


def _erpnext_customer_type(doc):
    """Native ERPNext Customer.customer_type for the invoice's customer."""
    if not doc.get("customer"):
        return None
    return frappe.db.get_value("Customer", doc.get("customer"), "customer_type")


def resolve_customer_dgi_type(doc, settings=None):
    """Effective DGI customer type code: explicit on invoice/customer, else derived from the
    native Customer Type via the mapping default (DGI Settings -> Customer Type Mapping)."""
    settings = settings or get_settings()
    ctype = doc.get("dgi_customer_type")
    if not ctype and doc.get("customer"):
        ctype = frappe.db.get_value("Customer", doc.get("customer"), "dgi_customer_type")
    if not ctype:
        ctype = settings.default_dgi_type_for_customer_type(_erpnext_customer_type(doc))
    return ctype or None


# --------------------------------------------------------------------------- #
# Matrix G - native Customer Type -> allowed DGI type
# --------------------------------------------------------------------------- #

def evaluate_customer_type_mapping(doc, settings=None):
    """Matrix G: the customer's DGI type must be allowed for its native ERPNext Customer Type."""
    settings = settings or get_settings()
    if not getattr(settings, "enforce_customer_type_map", 1):
        return []
    native = _erpnext_customer_type(doc)
    allowed = settings.dgi_types_for_customer_type(native)
    if not allowed:
        # No mapping configured for this native type -> nothing to enforce.
        return []
    effective = resolve_customer_dgi_type(doc, settings)
    allowed_txt = "/".join(allowed)
    if not effective:
        return [{
            "matrix": "G - Mapping Type de client",
            "enforcement": "Block",
            "message": _("Matrice G: Type de client DGI requis pour le Customer Type natif '{0}'. "
                         "Valeurs autorisees: {1}.").format(native, allowed_txt),
            "rule": native,
        }]
    if effective not in allowed:
        return [{
            "matrix": "G - Mapping Type de client",
            "enforcement": "Block",
            "message": _("Matrice G: Type de client DGI '{0}' non autorise pour le Customer Type "
                         "natif '{1}'. Valeurs autorisees: {2}.").format(effective, native, allowed_txt),
            "rule": native,
        }]
    return []


# --------------------------------------------------------------------------- #
# Invoice-type context rules (FV/EV default, FT/ET prepayment, FA/EA credit)
# --------------------------------------------------------------------------- #

def evaluate_invoice_type_context(doc, settings=None):
    """Coherence rules between the resolved DGI invoice type and the ERPNext invoice context."""
    settings = settings or get_settings()
    it = resolve_invoice_type(doc, settings)
    violations = []

    def block(msg):
        violations.append({"matrix": "Type de facture", "enforcement": "Block",
                           "message": msg, "rule": it})

    is_return = bool(doc.get("is_return"))
    is_export = bool(doc.get("custom_dgi_export"))

    if it not in ("FV", "FT", "FA", "EV", "ET", "EA"):
        block(_("Type de facture DGI invalide: {0}.").format(it))
        return violations

    # Returns / credit notes must be FA or EA, and conversely.
    if is_return and it not in CREDIT_TYPES:
        block(_("Un retour / avoir ERPNext doit etre de type FA ou EA (type actuel: {0}).").format(it))
    if it in CREDIT_TYPES and not (doc.get("return_against") or doc.get("custom_dgi_reference")):
        block(_("Type {0} (avoir): la reference d'origine (Code DEF/DGI) ou la facture liee est obligatoire.").format(it))

    # Prepayment types are never returns.
    if it in PREPAYMENT_TYPES and is_return:
        block(_("Type {0} (acompte/prepaiement) incompatible avec un retour ERPNext.").format(it))

    # Standard types are never returns.
    if it in STANDARD_TYPES and is_return:
        block(_("Type {0} incompatible avec un retour ERPNext (utilisez FA/EA).").format(it))

    # Export flag must be consistent with E* / F* family.
    if is_export and it not in EXPORT_TYPES:
        block(_("'Facture a l'exportation' cochee mais le type {0} n'est pas un type export (EV/ET/EA).").format(it))
    if (not is_export) and it in EXPORT_TYPES and not doc.get("dgi_invoice_type"):
        # Only enforce when the type was auto-derived; explicit export types are allowed.
        pass

    return violations


# --------------------------------------------------------------------------- #
# Matrix B - mandatory customer-card fields
# --------------------------------------------------------------------------- #

def _present(value):
    return bool(str(value or "").strip())


def _customer_field_present(doc, key):
    """True when the data backing requirement `key` is present on the invoice/customer."""
    if key == "req_bill_to_name":
        return _present(doc.get("customer_name") or doc.get("customer"))
    if key == "req_vat_reg_no":
        return _present(doc.get("tax_id"))
    if key == "req_contact":
        return _present(doc.get("contact_person") or doc.get("contact_display")
                        or doc.get("contact_email") or doc.get("contact_mobile"))
    if key == "req_address":
        return _present(doc.get("customer_address") or doc.get("address_display"))
    if key == "req_phone":
        return _present(doc.get("contact_mobile") or doc.get("contact_phone"))
    if key == "req_email":
        return _present(doc.get("contact_email"))
    if key == "req_registration_no":
        reg = doc.get("customer") and frappe.db.get_value(
            "Customer", doc.get("customer"), "tax_id")
        return _present(doc.get("custom_dgi_registration_no") or reg)
    return True


def evaluate_customer_fields(doc, settings=None):
    """Matrix B: returns a list of violation dicts for missing mandatory customer fields."""
    settings = settings or get_settings()
    ctype = resolve_customer_dgi_type(doc, settings)
    if not ctype or not frappe.db.exists("DGI Customer Type", ctype):
        return []
    ct = frappe.get_cached_doc("DGI Customer Type", ctype)
    from dgi_compliance.dgi_compliance.doctype.dgi_customer_type.dgi_customer_type import REQUIREMENT_FIELDS
    violations = []
    for key, label in REQUIREMENT_FIELDS.items():
        if (ct.get(key) or "Optional") == "Mandatory" and not _customer_field_present(doc, key):
            violations.append({
                "matrix": "B - Champs client",
                "enforcement": "Block",
                "message": _("Matrice B ({0}): champ obligatoire manquant - {1}.").format(ctype, label),
                "rule": ctype,
            })
    return violations


# --------------------------------------------------------------------------- #
# Matrix A - POS NID / Token (validated when a POS Profile is linked)
# --------------------------------------------------------------------------- #

def evaluate_point_of_sale(doc):
    """Matrix A: when the invoice is tied to a POS Profile mapped to a DGI Point of Sale that is
    a sales location, NID and Token must be present."""
    pos_profile = doc.get("pos_profile")
    if not pos_profile:
        return []
    dgi_pos = frappe.db.get_value("POS Profile", pos_profile, "dgi_point_of_sale")
    if not dgi_pos or not frappe.db.exists("DGI Point of Sale", dgi_pos):
        return []
    pos = frappe.get_cached_doc("DGI Point of Sale", dgi_pos)
    violations = []
    if (pos.nid_requirement or "Optional") == "Mandatory" and not _present(pos.nid):
        violations.append({"matrix": "A - Point de vente", "enforcement": "Block",
                           "message": _("Matrice A ({0}): NID obligatoire pour ce point de vente.").format(dgi_pos),
                           "rule": dgi_pos})
    token_ok = _present(pos.get_password("token", raise_exception=False)) or _present(get_settings().get_token())
    if (pos.token_requirement or "Optional") == "Mandatory" and not token_ok:
        violations.append({"matrix": "A - Point de vente", "enforcement": "Block",
                           "message": _("Matrice A ({0}): Token obligatoire pour ce point de vente.").format(dgi_pos),
                           "rule": dgi_pos})
    return violations


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #

def _can_override(settings):
    roles = [r.strip() for r in (getattr(settings, "override_roles", None) or "System Manager").split(",") if r.strip()]
    user_roles = set(frappe.get_roles())
    return any(r in user_roles for r in roles)


def collect_violations(doc, settings=None):
    """Run every matrix and return the list of violations (does not throw)."""
    settings = settings or get_settings()
    frappe.flags.pop("_dgi_matrix_cache", None)  # fresh rule snapshot per document

    invoice_type = resolve_invoice_type(doc, settings)
    country_scope = resolve_country_scope(doc, settings)
    credit_nature = resolve_credit_nature(doc)

    violations = []

    # Invoice-type context coherence (FV/EV/FT/ET/FA/EA)
    violations.extend(evaluate_invoice_type_context(doc, settings))

    # Matrix C - header level (Invoice Type x Country)
    v = _decide(MATRIX_C,
                {"invoice_type": invoice_type, "country_scope": country_scope},
                _("Matrice C (Facture/Pays)"))
    if v:
        violations.append(v)

    # Matrices D, E, F - per line
    for it in (doc.get("items") or []):
        item_type = resolve_item_type(it.get("item_code"))
        tax_group = resolve_tax_group(it, settings)
        line = f"#{it.get('idx')} {it.get('item_code') or it.get('item_name') or ''}".strip()

        vd = _decide(MATRIX_D, {"item_type": item_type, "tax_group": tax_group},
                     _("Matrice D ligne {0} (Article/Taxe)").format(line))
        if vd:
            violations.append(vd)

        ve = _decide(MATRIX_E, {"invoice_type": invoice_type, "item_type": item_type},
                     _("Matrice E ligne {0} (Acompte/Article)").format(line))
        if ve:
            violations.append(ve)

        if credit_nature:
            vf = _decide(MATRIX_F, {"credit_nature": credit_nature, "item_type": item_type},
                         _("Matrice F ligne {0} (Avoir/Article)").format(line))
            if vf:
                violations.append(vf)

    # Matrix A, B, G
    violations.extend(evaluate_point_of_sale(doc))
    violations.extend(evaluate_customer_fields(doc, settings))
    violations.extend(evaluate_customer_type_mapping(doc, settings))
    return violations


def validate_sales_invoice(doc, method=None):
    """Sales Invoice `validate` hook: the locking mechanism."""
    settings = get_settings()
    if not settings.enabled:
        return
    mode = (getattr(settings, "validation_enforcement", None) or "Enforce")
    if mode == "Off":
        return

    violations = collect_violations(doc, settings)
    if not violations:
        return

    overridden = bool(doc.get("dgi_validation_override")) and _can_override(settings)
    blocking = [v for v in violations if v["enforcement"] == "Block"]
    warnings = [v for v in violations if v["enforcement"] == "Warn"]

    # Audit every evaluation that produced at least one violation.
    try:
        from dgi_compliance.edef.audit import log_exchange
        log_exchange("matrix-validate", {"invoice": doc.name or doc.get("__newname")},
                     {"violations": violations, "overridden": overridden},
                     reference_invoice=doc.name)
    except Exception:
        pass

    for w in warnings:
        frappe.msgprint(w["message"], indicator="orange", title=_("Avertissement DGI"))

    if not blocking:
        return

    if mode == "Warn only" or overridden:
        head = _("Validation DGI ignoree (override)") if overridden else _("Avertissement DGI")
        frappe.msgprint("<br>".join(v["message"] for v in blocking), indicator="orange", title=head)
        return

    # Enforce: block the save.
    msg = "<br>".join("&bull; " + v["message"] for v in blocking)
    hint = _("Corrigez les donnees, ajustez la matrice concernee, ou cochez "
             "'Override validation DGI' (administrateur) pour forcer l'enregistrement.")
    frappe.throw(msg + "<br><br>" + hint, title=_("Combinaison non conforme DGI"))


def validate_point_of_sale_doc(doc, method=None):
    """Optional Customer hook placeholder kept for symmetry / future use."""
    return
