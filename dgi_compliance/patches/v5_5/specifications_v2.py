"""v5.5.0 - DGI specifications v2 (Note de service n°01/209/DGI/DG/DLCFI/FMM/2026, FACNO Groupes de
taxation v2.0, protocols MCF-SFE v2 and e-MCF-SFE v2, October 2026).

Idempotent, never deletes data, never overrides an administrator's choice:
  * tax groups: the 19 applicable groups A-S exist; group A is "Exonere" (hors champ moved to S);
    the reserved groups T-Z exist, flagged reserved and inactive ("Reserve 1..7");
  * DGI Compliance Settings: a default tax group in T-Z goes back to A; the hors champ rule is
    set to group S (specifications v2) when it was never chosen;
  * Matrix D rules on a reserved group are deactivated;
  * Tax Group Mapping rows on a reserved group are reported (they block the normalization).
The result is written to the journal (DGI Gateway Event, SPECS_V2).
"""
import frappe

from dgi_compliance.edef.calc import RESERVED_GROUPS, reserved_label


def execute():
    from dgi_compliance.edef.seed import TAX_GROUPS_FACNO
    notes = []

    # 1. Tax groups
    for code, (desc, rate, is_vat) in TAX_GROUPS_FACNO.items():
        if not frappe.db.exists("DGI Tax Group", code):
            frappe.get_doc({"doctype": "DGI Tax Group", "code": code, "description": desc, "rate": rate,
                            "is_vat": is_vat, "is_active": 1, "source": "Static"}).insert(ignore_permissions=True)
            notes.append(f"groupe {code} cree ({desc})")
    a = frappe.db.get_value("DGI Tax Group", "A", ["description", "manual_override"], as_dict=True)
    if a and not a.manual_override and "champ" in (a.description or "").lower():
        frappe.db.set_value("DGI Tax Group", "A", "description", TAX_GROUPS_FACNO["A"][0], update_modified=False)
        notes.append("groupe A : 'Exonere' (le hors champ passe au groupe S)")
    for code in RESERVED_GROUPS:
        if not frappe.db.exists("DGI Tax Group", code):
            frappe.get_doc({"doctype": "DGI Tax Group", "code": code, "description": reserved_label(code),
                            "rate": 0, "is_vat": 0, "is_active": 0, "is_reserved": 1,
                            "source": "Static"}).insert(ignore_permissions=True)
            continue
        row = frappe.db.get_value("DGI Tax Group", code, ["description", "is_active"], as_dict=True)
        updates = {"is_reserved": 1, "is_active": 0, "is_vat": 0}
        if not row.description or row.description == code:
            updates["description"] = reserved_label(code)
        if row.is_active:
            notes.append(f"groupe {code} desactive (reserve)")
        frappe.db.set_value("DGI Tax Group", code, updates, update_modified=False)

    # 2. Settings
    default = (frappe.db.get_single_value("DGI Compliance Settings", "default_tax_group") or "").upper()
    if default in RESERVED_GROUPS:
        frappe.db.set_single_value("DGI Compliance Settings", "default_tax_group", "A")
        notes.append(f"groupe par defaut {default} (reserve) remplace par A")
    if not frappe.db.get_single_value("DGI Compliance Settings", "hors_champ_rule"):
        frappe.db.set_single_value("DGI Compliance Settings", "hors_champ_rule", "Groupe S (specifications v2)")
    blocked = frappe.get_all("DGI Tax Group Map", filters={"edef_tax_group": ["in", list(RESERVED_GROUPS)]},
                             fields=["idx", "item_tax_template", "sales_taxes_template", "edef_tax_group"])
    for r in blocked:
        notes.append("Tax Group Mapping ligne #{0} ({1}) sur le groupe reserve {2} : a corriger, la normalisation "
                     "est refusee".format(r.idx, r.item_tax_template or r.sales_taxes_template or "?", r.edef_tax_group))

    # 3. Matrix D rules on a reserved group
    for name in frappe.get_all("DGI Validation Matrix", filters={"tax_group": ["in", list(RESERVED_GROUPS)],
                                                                 "is_active": 1}, pluck="name"):
        frappe.db.set_value("DGI Validation Matrix", name, "is_active", 0, update_modified=False)
        notes.append(f"regle {name} (groupe reserve) desactivee")

    hc = frappe.db.count("Item", {"dgi_hors_champ": 1}) if frappe.db.has_column("Item", "dgi_hors_champ") else 0
    if hc:
        notes.append(f"{hc} article(s) hors champ : declares desormais dans le groupe S")

    from dgi_compliance.gateway.events import log_event
    log_event(None, "SPECS_V2",
              "Mise a jour 5.5.0 (specifications DGI v2) : groupes A a S applicables, T a Z reserves, hors champ en "
              "groupe S, API e-MCF v2 et protocole MCF v2 (ST2). " + ("; ".join(notes) if notes else "Aucune correction."),
              level="Alerte" if blocked else "Info", details={"notes": notes})
