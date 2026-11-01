"""v5.3.0 - routage DGI (livrable B section 10). No call to the e-MCF or to an MCF, no invoice.

* canal prioritaire general = MCF when the old communication mode was 'MCF Physique (USB)', else e-MCF;
* 'Canal principal' of a point de vente becomes an override: blank when it equals the general priority,
  kept with the reason 'Reprise de la v5.2.0' otherwise;
* a v5.2 channel in force different from the priority: the last submitted DGI Channel Switch to that
  channel becomes the switch in force; without such a switch the channel is kept as an override, so
  that no point de vente changes channel at the upgrade;
* MCF 'Actif': MID validated = last MID read when it carries the NID of the MCF; otherwise (never read, or
  read with another NID) it goes back to 'Declare' (validation needed);
* DGI EMCF.point_of_sale mirrors the NIM of the points de vente; tokens are marked 'Non verifie'.
"""
import frappe

from dgi_compliance.fiscal.mcf_format import nid_of


def _single(field):
    r = frappe.db.sql("select value from `tabSingles` where doctype='DGI Compliance Settings' and field=%s", field)
    return r[0][0] if r else None


def _set_single(field, value):
    frappe.db.sql("delete from `tabSingles` where doctype='DGI Compliance Settings' and field=%s", field)
    frappe.db.sql("insert into `tabSingles` (doctype, field, value) values ('DGI Compliance Settings', %s, %s)",
                  (field, value))


def execute():
    frappe.reload_doc("dgi_compliance", "doctype", "dgi_compliance_settings")
    for dt in ("dgi_point_of_sale", "dgi_mcf_device", "dgi_fiscal_operation", "dgi_emcf", "dgi_channel_switch"):
        frappe.reload_doc("dgi_compliance", "doctype", dt)

    general = _single("primary_channel")
    if general not in ("e-MCF", "MCF"):
        general = "MCF" if (_single("communication_mode") or "") == "MCF Physique (USB)" else "e-MCF"
        _set_single("primary_channel", general)
    if not _single("mcf_fallback_after_s"):
        _set_single("mcf_fallback_after_s", "60")
    if not _single("warehouse_mismatch_action"):
        _set_single("warehouse_mismatch_action", "Signaler")

    for pvf in frappe.get_all("DGI Point of Sale", fields=["name", "primary_channel", "active_channel", "forced_switch",
                                                            "priority_reason", "nim", "token_check"]):
        values = {}
        override = pvf.primary_channel if pvf.primary_channel in ("e-MCF", "MCF") else None
        if override == general and not pvf.priority_reason:
            override = None
            values.update({"primary_channel": None, "priority_reason": None})
        elif override and not pvf.priority_reason:
            values["priority_reason"] = "Reprise de la v5.2.0 (canal principal)"
        priority = override or general
        active = pvf.active_channel if pvf.active_channel in ("e-MCF", "MCF") else None
        if active and active != priority and not pvf.forced_switch:
            switch = frappe.db.get_value("DGI Channel Switch", {"point_of_sale": pvf.name, "docstatus": 1,
                                                                 "to_channel": active}, "name", order_by="creation desc")
            if switch:
                values["forced_switch"] = switch
            else:
                values.update({"primary_channel": active, "priority_reason": "Reprise de la v5.2.0 (canal actif)"})
                priority = active
        if not active or (active != priority and not values.get("forced_switch") and not pvf.forced_switch):
            values["active_channel"] = priority
        if frappe.db.get_value("DGI Point of Sale", pvf.name, "token") is not None and not pvf.token_check:
            values["token_check"] = "Non verifie"
        if values:
            frappe.db.set_value("DGI Point of Sale", pvf.name, values, update_modified=False)
        if pvf.nim and frappe.db.exists("DGI EMCF", pvf.nim):
            frappe.db.set_value("DGI EMCF", pvf.nim, "point_of_sale", pvf.name, update_modified=False)

    for dev in frappe.get_all("DGI MCF Device", filters={"status": "Actif"}, fields=["name", "last_mid", "validated_mid"]):
        if dev.validated_mid:
            continue
        if dev.last_mid and nid_of(dev.last_mid) == dev.name:
            frappe.db.set_value("DGI MCF Device", dev.name, {"validated_mid": dev.last_mid}, update_modified=False)
        else:
            frappe.db.set_value("DGI MCF Device", dev.name, {"status": "Declare"}, update_modified=False)

    frappe.db.sql("""update `tabDGI Fiscal Operation` set channel='MCF' where ifnull(channel, '') = ''""")
    frappe.clear_document_cache("DGI Point of Sale")
    frappe.clear_cache(doctype="DGI Compliance Settings")
