"""v5.2.0 (HL2) - e-MCF principal with MCF fallback, browser stations, H5/H6 decisions.

* existing site gateways (dgi-gateway program, Ed25519 keys) become kind "Service (poste dedie)";
* points de vente without a primary channel: e-MCF; automatic fallback when an MCF is attached;
* credit sales are declared on the MCF (H5): the policy "Bloquer" of v5.1 becomes the declaration
  with the credit letter of the updated MCF (to configure);
* manual switches no longer require a DGI reference (H1/H2 confirmed).
"""
import frappe


def execute():
    for gw in frappe.get_all("DGI Gateway", fields=["name", "kind", "public_key", "key_algorithm"]):
        values = {}
        if gw.public_key and not gw.key_algorithm:
            values["key_algorithm"] = "Ed25519"
        if gw.public_key and gw.kind != "Service (poste dedie)" and (values.get("key_algorithm") or gw.key_algorithm) == "Ed25519":
            values["kind"] = "Service (poste dedie)"
        if values:
            frappe.db.set_value("DGI Gateway", gw.name, values, update_modified=False)
    for pvf in frappe.get_all("DGI Point of Sale", fields=["name", "primary_channel", "fallback_policy", "mcf_device"]):
        values = {}
        if not pvf.primary_channel:
            values["primary_channel"] = "e-MCF"
        if not pvf.fallback_policy:
            values["fallback_policy"] = "Automatique"
        if values:
            frappe.db.set_value("DGI Point of Sale", pvf.name, values, update_modified=False)
    if frappe.db.get_single_value("DGI Compliance Settings", "mcf_credit_policy") in (None, "", "Bloquer"):
        frappe.db.set_single_value("DGI Compliance Settings", "mcf_credit_policy",
                                   "Declarer avec la lettre credit du MCF")
    for field, value in (("switch_requires_dgi_reference", 0), ("emcf_retry_minutes", 5),
                         ("station_poll_interval_s", 15), ("station_approval_two_people", 1)):
        stored = frappe.db.sql("select value from `tabSingles` where doctype=%s and field=%s",
                               ("DGI Compliance Settings", field))
        if not stored or stored[0][0] in (None, ""):
            frappe.db.set_single_value("DGI Compliance Settings", field, value)
