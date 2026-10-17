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


def resolve_tax_group(doc_item, settings, doc=None):
    """Tax group used by the validation matrices. Non-strict: falls back to the default
    group so matrix checks never crash; the strict mapping check lives in the mapper."""
    if doc is not None:
        return settings.tax_group_for_invoice(doc, doc_item) or (settings.default_tax_group or "A")
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
    if it in CREDIT_TYPES and not (doc.get("return_against") or doc.get("custom_dgi_reference")
                                   or (doc.get("custom_dgi_reference_type") or "").upper() == "RRR"):
        block(_("Type {0} (avoir): la reference d'origine (Code DEF/DGI) ou la facture liee est obligatoire.").format(it))

    # Prepayment types are never returns.
    if it in PREPAYMENT_TYPES and is_return:
        block(_("Type {0} (acompte/prepaiement) incompatible avec un retour ERPNext.").format(it))

    # Standard types are never returns.
    if it in STANDARD_TYPES and is_return:
        block(_("Type {0} incompatible avec un retour ERPNext (utilisez FA/EA).").format(it))

    # A credit nature (rabais RRR, correction, annulation...) requires an avoir type FA/EA.
    if doc.get("custom_dgi_reference_type") and it not in CREDIT_TYPES:
        block(_("Une 'Nature de l'avoir' ({0}) est renseignee: le type doit etre FA ou EA (type actuel: {1}).")
              .format(doc.get("custom_dgi_reference_type"), it))

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
            "Customer", doc.get("customer"), "dgi_registration_no")
        return _present(doc.get("custom_dgi_registration_no") or reg
                        or (doc.get("customer") and frappe.db.get_value("Customer", doc.get("customer"), "tax_id")))
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
# v5.0.0 - legal controls (not overridable)
# --------------------------------------------------------------------------- #

def _hard(matrix, msg, rule=None):
    return {"matrix": matrix, "enforcement": "Block", "message": msg, "rule": rule, "hard": True}


def evaluate_line_amounts(doc):
    """SFE 1.1.21/1.1.22: no line, and no invoice, with a nil or negative amount."""
    out = []
    for it in (doc.get("items") or []):
        if not flt(it.get("qty")) or not (abs(flt(it.get("rate"))) > 0) or not abs(flt(it.get("net_amount"))):
            out.append(_hard("Montants", _("Ligne #{0} ({1}): quantite, prix et montant doivent etre "
                                           "superieurs a zero (SFE 1.1.22).").format(
                it.get("idx"), it.get("item_code") or it.get("item_name") or "")))
    if (doc.get("items") or []) and not abs(flt(doc.get("grand_total"))):
        out.append(_hard("Montants", _("Le montant de la facture ne peut pas etre nul (SFE 1.1.21).")))
    return out


def evaluate_exemption_reference(doc, settings=None):
    """Group D (regime derogatoire) or AO client: the exemption reference must be given in
    'Commentaire A' (FACNO groupes de taxation; SFE annexe 'Lignes de commentaires')."""
    settings = settings or get_settings()
    if (doc.get("dgi_comment_a") or "").strip():
        return []
    reasons = []
    if resolve_customer_dgi_type(doc, settings) == "AO":
        reasons.append(_("client de type AO"))
    for it in (doc.get("items") or []):
        if resolve_tax_group(it, settings, doc) == "D":
            reasons.append(_("article du groupe D (ligne #{0})").format(it.get("idx")))
            break
    if not reasons:
        return []
    return [_hard("Exoneration", _("Commentaire A obligatoire ({0}): saisissez la reference du "
                                   "certificat / document d'exoneration delivre par la DGI.").format(", ".join(reasons)))]


def evaluate_credit_note(doc, settings=None):
    """SFE 1.1.25-1.1.28: nature mandatory; reference = Code DEF/DGI of the normalized original
    (or 'RRR'); for a cancellation / take-back the unit price equals the original price."""
    settings = settings or get_settings()
    if resolve_invoice_type(doc, settings) not in CREDIT_TYPES:
        return []
    out = []
    nature = (doc.get("custom_dgi_reference_type") or "").upper()
    if nature not in ("COR", "RAN", "RAM", "RRR"):
        out.append(_hard("Avoir", _("Nature de l'avoir obligatoire: COR (Correction), RAN (Annulation), "
                                    "RAM (Avoir suite reprise) ou RRR (Remise, ristourne, rabais).")))
    original = doc.get("return_against")
    if original:
        code = frappe.db.get_value("Sales Invoice", original, "custom_dgi_code_def")
        if not code:
            out.append(_hard("Avoir", _("La facture d'origine {0} n'est pas normalisee (pas de Code DEF/DGI): "
                                        "l'avoir ne peut pas la referencer.").format(original)))
        manual = (doc.get("custom_dgi_reference") or "").strip()
        if manual and code and manual != code:
            out.append(_hard("Avoir", _("La reference saisie ({0}) differe du Code DEF/DGI de la facture "
                                        "d'origine ({1}).").format(manual, code)))
        if nature in ("RAN", "RAM"):
            orig_rates = {}
            for r in frappe.get_all("Sales Invoice Item", filters={"parent": original},
                                    fields=["name", "item_code", "rate"]):
                orig_rates[r.name] = abs(flt(r.rate))
                orig_rates.setdefault(("code", r.item_code), abs(flt(r.rate)))
            for it in (doc.get("items") or []):
                ref = orig_rates.get(it.get("sales_invoice_item")) if it.get("sales_invoice_item") \
                    else orig_rates.get(("code", it.get("item_code")))
                if ref is None:
                    out.append(_hard("Avoir", _("Ligne #{0}: l'article {1} n'existe pas sur la facture "
                                                "d'origine.").format(it.get("idx"), it.get("item_code"))))
                elif abs(abs(flt(it.get("rate"))) - ref) > 0.01:
                    out.append(_hard("Avoir", _("Ligne #{0}: le prix unitaire ({1}) doit etre egal au prix de "
                                                "la facture d'origine ({2}) (SFE 1.1.26 e).").format(
                        it.get("idx"), abs(flt(it.get("rate"))), ref)))
    elif nature != "RRR":
        ref = (doc.get("custom_dgi_reference") or "").strip()
        if len(ref.replace("-", "")) != 24:
            out.append(_hard("Avoir", _("Avoir sans facture liee: renseignez le Code DEF/DGI d'origine "
                                        "(24 caracteres) ou choisissez la nature RRR.")))
    return out


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
        tax_group = resolve_tax_group(it, settings, doc)
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

    # v5.0.0 legal controls
    violations.extend(evaluate_line_amounts(doc))
    violations.extend(evaluate_exemption_reference(doc, settings))
    violations.extend(evaluate_credit_note(doc, settings))

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
        violations = (evaluate_line_amounts(doc) + evaluate_exemption_reference(doc, settings)
                      + evaluate_credit_note(doc, settings))
    else:
        violations = collect_violations(doc, settings)
    if not violations:
        return

    overridden = bool(doc.get("dgi_validation_override")) and _can_override(settings)
    hard = [v for v in violations if v.get("hard")]
    violations = [v for v in violations if not v.get("hard")]
    if hard:
        try:
            from dgi_compliance.edef.audit import log_exchange
            log_exchange("legal-validate", {"invoice": doc.name or doc.get("__newname")},
                         {"violations": hard}, reference_invoice=doc.name)
        except Exception:
            pass
        frappe.throw("<br>".join("&bull; " + v["message"] for v in hard),
                     title=_("Facture non conforme DGI"))
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


def _check_rate_deviation(doc, settings):
    """SFE 1.1.30: the exchange rate must stay within 1% of the reference rate (BCC average
    rate as published by the e-MCF /api/info/currencyRates, synchronised in DGI Reference Value)."""
    control = getattr(settings, "usd_rate_control", None) or "Bloquer"
    if control == "Desactive":
        return
    from dgi_compliance.edef.mapper import _foreign_ccy, _cur_rate
    ccy = _foreign_ccy(doc)
    if not ccy:
        return
    ref = flt(frappe.db.get_value("DGI Reference Value", f"Currency Rate::{ccy}", "value"))
    if not ref:
        return
    used = flt(_cur_rate(doc))
    deviation = abs(used - ref) / ref
    limit = flt(getattr(settings, "usd_rate_max_deviation", None) or 1.0) / 100.0
    if deviation <= limit:
        return
    msg = _("Taux de change {0} = {1} CDF: ecart de {2}% avec le taux de reference DGI/BCC ({3} CDF). "
            "Ecart maximum autorise: {4}% (SFE 1.1.30).").format(
        ccy, used, round(deviation * 100, 2), ref, round(limit * 100, 2))
    if control == "Alerte":
        frappe.msgprint(msg, indicator="orange", title=_("Taux de change DGI"))
    else:
        frappe.throw(msg, title=_("Taux de change hors tolerance"))


def validate_currency(doc, method=None):
    """`validate` hook: enforce the DGI currency matrix (authorized currencies per invoice type).
    Independent from the A-G matrices switch; governed by Settings.currency_enforcement."""
    settings = get_settings()
    if not settings.enabled:
        return
    _check_rate_deviation(doc, settings)
    mode = getattr(settings, "currency_enforcement", None) or "Enforce"
    if mode == "Off" or not (settings.currency_rules or []):
        return
    invoice_type = resolve_invoice_type(doc, settings)
    ccy = doc.get("currency")
    if settings.currency_allowed(ccy, invoice_type):
        return
    msg = _("Devise {0} non autorisee pour le type de facture {1} (matrice des devises DGI).").format(ccy, invoice_type)
    try:
        from dgi_compliance.edef.audit import log_exchange
        log_exchange("currency-validate",
                     {"invoice": doc.name or doc.get("__newname"), "currency": ccy, "invoice_type": invoice_type},
                     {"allowed": False}, reference_invoice=doc.name)
    except Exception:
        pass
    overridden = bool(doc.get("dgi_validation_override")) and _can_override(settings)
    if mode == "Warn only" or overridden:
        frappe.msgprint(msg, indicator="orange", title=_("Avertissement devise DGI"))
        return
    frappe.throw(msg + "<br><br>" + _("Ajoutez la devise a la matrice (DGI Compliance Settings > "
                 "Devises) ou cochez 'Override validation DGI'."), title=_("Devise non autorisee"))


def validate_point_of_sale_doc(doc, method=None):
    """Optional Customer hook placeholder kept for symmetry / future use."""
    return
