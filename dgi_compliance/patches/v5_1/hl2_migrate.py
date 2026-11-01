"""v5.1.0 (HL2) - channel per point de vente fiscal.

* each DGI Point of Sale gets a company (when the site has a single company) and an active
  channel equal to the former global mode of DGI Compliance Settings;
* queued jobs of the v4 agent (DGI MCF Job) are cancelled: the v4 agent is replaced by the
  site gateways (DGI Gateway) and must not process anything anymore.
"""
import frappe


def execute():
    mode = frappe.db.get_single_value("DGI Compliance Settings", "communication_mode") or "E-MCF (API)"
    default_channel = "MCF" if mode == "MCF Physique (USB)" else "e-MCF"
    companies = frappe.get_all("Company", pluck="name")
    for pvf in frappe.get_all("DGI Point of Sale", fields=["name", "company", "active_channel", "primary_channel"]):
        values = {}
        if not pvf.company and len(companies) == 1:
            values["company"] = companies[0]
        if not pvf.active_channel:
            # a former MCF site has no MCF device declared yet: stay on e-MCF until it is configured
            values["active_channel"] = "e-MCF"
            values["primary_channel"] = pvf.primary_channel or default_channel
        if values:
            frappe.db.set_value("DGI Point of Sale", pvf.name, values, update_modified=False)
    if frappe.db.table_exists("DGI MCF Job"):
        frappe.db.sql("""update `tabDGI MCF Job` set status='Cancelled',
                         error='Agent v4 remplace par les passerelles DGI Gateway (v5.1)'
                         where status='Queued'""")
