"""v5.0.0 (HL1) migration - idempotent, never deletes data, never overrides an administrator's
own choice (only values still equal to the shipped defaults are updated)."""
import frappe
from frappe.utils import get_datetime


def execute():
    # Patches run before the fixtures are synced: create the v5 custom fields first.
    from frappe.utils.fixtures import sync_fixtures
    sync_fixtures("dgi_compliance")
    frappe.clear_cache(doctype="Sales Invoice")
    _settings()
    _tax_groups()
    _customer_types()
    _customer_type_map()
    _matrices()
    _backfill_normalized_on()
    _default_print_format()


def _settings():
    values = {
        "normalization_gating": "Enforce",  # posting gate without exception (SFE 1.1.5)
        "emcf_check_action": "Bloquer",
        "emcf_check_tolerance": 1,
        "finalize_action_case": "minuscules",
    }
    for field, value in values.items():
        current = frappe.db.get_single_value("DGI Compliance Settings", field)
        if field == "normalization_gating" or not current:
            frappe.db.set_single_value("DGI Compliance Settings", field, value)


def _tax_groups():
    from dgi_compliance.edef.seed import TAX_GROUPS_FACNO
    for code, (desc, rate, is_vat) in TAX_GROUPS_FACNO.items():
        if not frappe.db.exists("DGI Tax Group", code):
            frappe.get_doc({"doctype": "DGI Tax Group", "code": code, "description": desc, "rate": rate,
                            "is_vat": is_vat, "is_active": 1, "source": "Static"}).insert(ignore_permissions=True)
            continue
        row = frappe.db.get_value("DGI Tax Group", code,
                                  ["description", "rate", "source", "manual_override"], as_dict=True)
        if row.manual_override:
            continue
        updates = {}
        if not row.description or row.description.startswith("Groupe de taxe"):
            updates["description"] = desc
        if row.source != "API" and not row.rate and rate:
            updates["rate"] = rate
        if updates:
            frappe.db.set_value("DGI Tax Group", code, updates, update_modified=False)


def _customer_types():
    from dgi_compliance.edef.seed import CUSTOMER_TYPES, CUSTOMER_TYPES_V4, _REQ_ORDER
    new = {code: reqs for code, _desc, reqs in CUSTOMER_TYPES}
    for code, old in CUSTOMER_TYPES_V4.items():
        if not frappe.db.exists("DGI Customer Type", code):
            continue
        current = frappe.db.get_value("DGI Customer Type", code, _REQ_ORDER, as_dict=True)
        if [current.get(f) or "Optional" for f in _REQ_ORDER] == old:
            frappe.db.set_value("DGI Customer Type", code, dict(zip(_REQ_ORDER, new[code])),
                                update_modified=False)


def _customer_type_map():
    """PC (personne physique commercante) and AO (ambassades / organisations internationales)
    were not allowed by the v3-v4 Matrix G seed: add them as non-default choices."""
    from dgi_compliance.edef.seed import CUSTOMER_TYPE_MAP
    settings = frappe.get_single("DGI Compliance Settings")
    existing = {(r.erpnext_customer_type, r.dgi_customer_type) for r in settings.customer_type_map or []}
    if not existing:
        return
    added = False
    for native, code, is_default in CUSTOMER_TYPE_MAP:
        if (native, code) not in existing and frappe.db.exists("DGI Customer Type", code):
            settings.append("customer_type_map", {"erpnext_customer_type": native,
                                                  "dgi_customer_type": code, "is_default": 0})
            added = True
    if added:
        settings.flags.ignore_mandatory = True
        settings.save(ignore_permissions=True)


def _matrices():
    """Credit notes on goods were blocked by the v2-v4 seed: contrary to SFE 1.1.26 (goods
    returned / cancelled). Only the untouched seeded rows are switched to Allow."""
    for matrix_type, field, values in (
            ("E - Prepayment / Item Type", "invoice_type", ("FA", "EA")),
            ("F - Credit Nature / Item Type", "credit_nature", ("RRR",))):
        for name in frappe.get_all("DGI Validation Matrix", filters={
                "matrix_type": matrix_type, field: ["in", values], "item_type": "BIE",
                "status": "Blocked", "message": ["is", "not set"]}, pluck="name"):
            frappe.db.set_value("DGI Validation Matrix", name, "status", "Allow", update_modified=False)


def _backfill_normalized_on():
    rows = frappe.get_all("Sales Invoice",
                          filters={"custom_dgi_code_def": ["is", "set"], "custom_dgi_normalized_on": ["is", "not set"]},
                          fields=["name", "custom_dgi_datetime", "posting_date", "posting_time"])
    for r in rows:
        value = None
        raw = (r.custom_dgi_datetime or "").strip()
        for fmt in ("%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                from datetime import datetime
                value = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
        if not value:
            value = get_datetime(f"{r.posting_date} {r.posting_time or '00:00:00'}")
        frappe.db.set_value("Sales Invoice", r.name, "custom_dgi_normalized_on", value, update_modified=False)


def _default_print_format():
    from dgi_compliance.edef.seed import set_default_print_format
    set_default_print_format()
