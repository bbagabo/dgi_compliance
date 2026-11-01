"""Server-side helpers of the browser end-to-end test (tests/js/e2e_station.js, Playwright).

    bench --site <site> execute dgi_compliance.tests.e2e_station.prepare
    bench --site <site> execute dgi_compliance.tests.e2e_station.approve --kwargs '{"fingerprint": "..."}'
    bench --site <site> execute dgi_compliance.tests.e2e_station.new_invoice
    bench --site <site> execute dgi_compliance.tests.e2e_station.invoice_state --kwargs '{"name": "..."}'
TEST SITES ONLY. Point de vente "E2E WEB": e-MCF principal (the test e-MCF is unreachable from the
test machine, which is exactly a DGI outage), MCF WEB0000001 as automatic fallback, driven by the
browser station GW-WEB-E2E operated by the cashier caisse.e2e@example.com.
"""
import frappe

from dgi_compliance.tests import fixtures as fx

PVF, NID, GW = "E2E WEB", "WEB0000001", "GW-WEB-E2E"
CASHIER, PASSWORD = "caisse.e2e@example.com", "Caisse#E2E-2026!"


def prepare():
    frappe.set_user("Administrator")
    if not frappe.conf.get("allow_tests"):
        frappe.throw("allow_tests requis")
    fx.setup_all()
    if not frappe.db.exists("User", CASHIER):
        u = frappe.get_doc({"doctype": "User", "email": CASHIER, "first_name": "Caisse E2E", "user_type": "System User",
                            "send_welcome_email": 0, "roles": [{"role": "DGI Caisse MCF"}]})
        u.flags.no_welcome_mail = True
        u.insert(ignore_permissions=True)
    from frappe.utils.password import update_password
    update_password(CASHIER, PASSWORD)
    if not frappe.db.exists("DGI MCF Device", NID):
        frappe.get_doc({"doctype": "DGI MCF Device", "nid": NID, "status": "Declare"}).insert()
    values = {"pos_name": PVF, "company": fx.COMPANY, "is_active": 1, "is_sales_location": 1, "nid": NID,
              "primary_channel": "e-MCF", "priority_reason": "E2E : e-MCF prioritaire", "mcf_device": NID,
              "fallback_policy": "Automatique", "token": "TOKEN-E2E-WEB",
              "nid_requirement": "Optional", "token_requirement": "Optional"}
    if frappe.db.exists("DGI Point of Sale", PVF):
        pvf = frappe.get_doc("DGI Point of Sale", PVF)
        pvf.update(values)
        pvf.flags.dgi_channel_switch = True
        pvf.save(ignore_permissions=True)
    else:
        frappe.get_doc(dict(values, doctype="DGI Point of Sale", active_channel="e-MCF")).insert()
    frappe.db.set_value("DGI MCF Device", NID, {"status": "Actif", "point_of_sale": PVF, "last_tc": 0, "tax_rates": None})
    s = frappe.get_single("DGI Compliance Settings")
    s.request_timeout = 3
    s.flags.ignore_mandatory = True
    s.save(ignore_permissions=True)
    from dgi_compliance.dgi_compliance.doctype.dgi_gateway.dgi_gateway import generate_enrollment_code, revoke_gateway
    if frappe.db.exists("DGI Gateway", GW):
        if frappe.db.get_value("DGI Gateway", GW, "status") != "Revoquee":
            revoke_gateway(GW, "e2e reset")
        frappe.db.set_value("DGI Gateway", GW, {"status": "Nouvelle", "public_key": None, "enrolled_by": None})
    else:
        frappe.get_doc({"doctype": "DGI Gateway", "gateway_id": GW, "kind": "Poste navigateur", "point_of_sale": PVF}).insert()
    from dgi_compliance.fiscal import fallback as FB
    frappe.cache.delete(FB._key(PVF))
    code = generate_enrollment_code(GW)["code"]
    frappe.db.commit()
    print("CODE=" + code)


def approve(fingerprint):
    frappe.set_user("Administrator")
    from dgi_compliance.dgi_compliance.doctype.dgi_gateway.dgi_gateway import approve_gateway
    res = approve_gateway(GW, fingerprint)
    frappe.db.commit()
    print("APPROVED=" + res["status"])


def new_invoice():
    frappe.set_user("Administrator")
    si = fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 1, 10000), ("DGI-A-EAU", 2, 1500)],
                         template="DGI TVA HT", dgi_point_of_sale=PVF)
    from dgi_compliance.edef.tasks import normalize_sales_invoice
    res = normalize_sales_invoice(si.name)
    frappe.db.commit()
    print("INVOICE=" + si.name + " STAGE=" + str(res.get("stage")))


def invoice_state(name):
    si = frappe.get_doc("Sales Invoice", name)
    op = frappe.db.get_value("DGI Fiscal Operation", si.custom_dgi_operation,
                             ["status", "gateway", "station_user", "fallback", "modified_by"], as_dict=True)
    events = frappe.get_all("DGI Gateway Event", filters={"gateway": GW}, pluck="event_code", order_by="creation desc", limit=8)
    print("STATE=" + frappe.as_json({"status": si.custom_dgi_status, "channel": si.custom_dgi_channel,
                                     "code_def": si.custom_dgi_code_def, "nim": si.custom_dgi_nim, "operation": op,
                                     "events": events,
                                     "fallback_since": str(frappe.db.get_value("DGI Point of Sale", PVF, "fallback_since"))},
                                    indent=None))


# --------------------------------------------------------------------------- v5.3.0 : decouverte (e2e_discovery.js)
PVF_D, GW_D, NID_D = "E2E DECOUVERTE", "GW-WEB-DISC", "DSC0000001"


def prepare_discovery():
    """Point de vente without MCF, browser station without MCF: the cashier reads and proposes the MCF."""
    frappe.set_user("Administrator")
    if not frappe.conf.get("allow_tests"):
        frappe.throw("allow_tests requis")
    prepare_user()
    for name in (NID_D,):
        if frappe.db.exists("DGI MCF Device", name):
            frappe.db.set_value("DGI Point of Sale", {"mcf_device": name}, "mcf_device", None)
            frappe.db.delete("DGI MCF Device", name)
    if frappe.db.exists("DGI Gateway", GW_D):
        frappe.db.delete("DGI Gateway", GW_D)
    if not frappe.db.exists("DGI Point of Sale", PVF_D):
        frappe.get_doc({"doctype": "DGI Point of Sale", "pos_name": PVF_D, "company": fx.COMPANY, "is_active": 1,
                        "is_sales_location": 0, "nid_requirement": "Optional", "token_requirement": "Optional",
                        "pos_address": "Boulevard du 30 juin", "pos_city": "Kinshasa"}).insert()
    frappe.db.set_value("DGI Point of Sale", PVF_D, "mcf_device", None)
    frappe.get_doc({"doctype": "DGI Gateway", "gateway_id": GW_D, "kind": "Poste navigateur", "point_of_sale": PVF_D}).insert()
    frappe.db.commit()
    print("READY=" + GW_D)


def prepare_user():
    if not frappe.db.exists("User", CASHIER):
        u = frappe.get_doc({"doctype": "User", "email": CASHIER, "first_name": "Caisse E2E", "user_type": "System User",
                            "send_welcome_email": 0, "roles": [{"role": "DGI Caisse MCF"}]})
        u.flags.no_welcome_mail = True
        u.insert(ignore_permissions=True)
    from frappe.utils.password import update_password
    update_password(CASHIER, PASSWORD)


def discovery_state():
    dev = frappe.db.get_value("DGI MCF Device", NID_D, ["status", "nif", "last_mid", "taxpayer_name", "taxpayer_address",
                                                       "discovered_by", "validated_by", "last_connectivity_result"],
                              as_dict=True) or {}
    print("STATE=" + frappe.as_json({"device": dev, "gateway_mcf": frappe.db.get_value("DGI Gateway", GW_D, "mcf_device"),
                                     "pvf_mcf": frappe.db.get_value("DGI Point of Sale", PVF_D, "mcf_device")}, indent=None))


def validate_discovered():
    """The manager (another person than the cashier who proposed it) validates the MCF."""
    frappe.set_user("Administrator")
    from dgi_compliance.fiscal.registry import validate_device
    res = validate_device(NID_D, PVF_D)
    frappe.db.commit()
    print("VALIDATED=" + res["status"])
