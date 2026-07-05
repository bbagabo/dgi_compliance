"""v3.1 - repair custom fields whose Link/Table 'options' is not a valid DocType.

Symptom fixed: saving Customize Form (Sales Invoice) - e.g. to set the default print format -
fails with "Options must be a valid DocType for field 'DGI eMCF POS' (row 72)". A Link/Table
custom field pointing to a non-existent DocType makes the whole DocType meta invalid.

Strategy (safe, idempotent):
  * scan Custom Fields whose fieldtype is Link / Table / Table MultiSelect;
  * if 'options' is empty or not an existing DocType:
      - if the field clearly targets the e-MCF / point of sale, repoint it to a valid DocType
        ('DGI EMCF') so it keeps working;
      - otherwise delete the broken (non-functional) custom field.
  * everything done is written to the Error Log for traceability.
"""
import frappe

LINK_TYPES = ("Link", "Table", "Table MultiSelect")
POS_TARGET = "DGI EMCF"  # valid DocType for an e-MCF / point-of-sale reference


def _is_valid_doctype(name):
    return bool(name) and frappe.db.exists("DocType", name)


def execute():
    actions = []
    fields = frappe.get_all(
        "Custom Field",
        filters={"fieldtype": ["in", LINK_TYPES]},
        fields=["name", "dt", "fieldname", "label", "fieldtype", "options"],
    )
    for f in fields:
        if _is_valid_doctype(f.options):
            continue  # healthy
        token = f"{f.fieldname} {f.label or ''}".lower()
        looks_like_pos = ("emcf" in token) or ("pos" in token and "dgi" in token)
        try:
            if looks_like_pos and _is_valid_doctype(POS_TARGET):
                frappe.db.set_value("Custom Field", f.name, "options", POS_TARGET)
                actions.append(f"repointed {f.dt}.{f.fieldname} -> {POS_TARGET} (was {f.options!r})")
            else:
                frappe.delete_doc("Custom Field", f.name, ignore_permissions=True, force=True)
                actions.append(f"deleted broken field {f.dt}.{f.fieldname} (invalid options {f.options!r})")
        except Exception as e:
            actions.append(f"FAILED on {f.dt}.{f.fieldname}: {e}")

    if actions:
        frappe.db.commit()
        try:
            frappe.clear_cache(doctype="Sales Invoice")
        except Exception:
            pass
        frappe.log_error(title="[DGI] v3.1 fix_invalid_link_fields",
                         message="\n".join(actions))
