"""v5.4.3 - "Operateur DGI" of the invoices normalized by an MCF.

Before 5.4.3 the MCF channel wrote the operator code of the MCF (16 characters, e.g. 'b.olinga') into
custom_dgi_operator, a Link to User: the invoice was normalized but could not be submitted ("Impossible de
trouver Operateur DGI : b.olinga"). The field gets back the ERPNext user who asked for the normalization (else the
creator of the invoice). Nothing sent to the DGI changes: the code recorded by the MCF stays in the stored request
and is still the one printed. Each repaired invoice is written to the journal.
"""
import frappe


def execute():
    rows = frappe.db.sql("""select si.name, si.owner, si.custom_dgi_operator, si.custom_dgi_operation,
                                   si.dgi_point_of_sale
                            from `tabSales Invoice` si
                            where si.custom_dgi_channel = 'MCF'
                              and ifnull(si.custom_dgi_operator, '') != ''
                              and not exists (select 1 from `tabUser` u where u.name = si.custom_dgi_operator)""",
                         as_dict=True)
    if not rows:
        return
    from dgi_compliance.gateway.events import log_event
    for r in rows:
        requested_by = frappe.db.get_value("DGI Fiscal Operation", r.custom_dgi_operation, "requested_by") \
            if r.custom_dgi_operation else None
        user = next((u for u in (requested_by, r.owner)
                     if u and u != "Guest" and frappe.db.exists("User", u)), None)
        frappe.db.set_value("Sales Invoice", r.name, "custom_dgi_operator", user, update_modified=False)
        log_event(None, "OPERATEUR_CORRIGE",
                  "Facture {0} : operateur DGI '{1}' (code du MCF) remplace par l'utilisateur {2} (mise a jour "
                  "5.4.3). Rien de ce qui a ete transmis a la DGI ne change.".format(r.name, r.custom_dgi_operator,
                                                                                     user or "(aucun)"),
                  point_of_sale=r.dgi_point_of_sale)
