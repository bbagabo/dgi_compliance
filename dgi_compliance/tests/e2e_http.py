"""Server-side helpers of the HTTP end-to-end test (dgi_gateway/tests/e2e_http_client.py).

    bench --site <site> execute dgi_compliance.tests.e2e_http.prepare
    bench --site <site> execute dgi_compliance.tests.e2e_http.approve
    bench --site <site> execute dgi_compliance.tests.e2e_http.new_invoice
    bench --site <site> execute dgi_compliance.tests.e2e_http.invoice_state --kwargs '{"name": "..."}'
TEST SITES ONLY.
"""
import frappe

from dgi_compliance.tests import fixtures as fx

PVF, NID, GW = "E2E LEMBA", "E2E0000001", "GW-E2E-01"


def prepare():
    frappe.set_user("Administrator")
    if not frappe.conf.get("allow_tests"):
        frappe.throw("allow_tests requis")
    fx.setup_all()
    if not frappe.db.exists("DGI MCF Device", NID):
        frappe.get_doc({"doctype": "DGI MCF Device", "nid": NID, "status": "Declare"}).insert()
    if not frappe.db.exists("DGI Point of Sale", PVF):
        frappe.get_doc({"doctype": "DGI Point of Sale", "pos_name": PVF, "company": fx.COMPANY, "is_active": 1,
                        "is_sales_location": 1, "nid": NID, "primary_channel": "MCF", "mcf_device": NID,
                        "priority_reason": "E2E : MCF prioritaire",
                        "nid_requirement": "Mandatory", "token_requirement": "Optional"}).insert()
    frappe.db.set_value("DGI MCF Device", NID, {"status": "Actif", "point_of_sale": PVF, "last_tc": 0})   # fresh simulator
    if frappe.db.exists("DGI Gateway", GW):
        from dgi_compliance.dgi_compliance.doctype.dgi_gateway.dgi_gateway import revoke_gateway
        if frappe.db.get_value("DGI Gateway", GW, "status") != "Revoquee":
            revoke_gateway(GW, "e2e reset")
        frappe.db.set_value("DGI Gateway", GW, "status", "Nouvelle")
    else:
        frappe.get_doc({"doctype": "DGI Gateway", "gateway_id": GW, "kind": "Service (poste dedie)",
                        "point_of_sale": PVF}).insert()
    from dgi_compliance.dgi_compliance.doctype.dgi_gateway.dgi_gateway import generate_enrollment_code
    code = generate_enrollment_code(GW)["code"]
    frappe.db.commit()
    print("CODE=" + code)
    return code


def approve():
    frappe.set_user("Administrator")
    from dgi_compliance.dgi_compliance.doctype.dgi_gateway.dgi_gateway import approve_gateway
    fp = frappe.db.get_value("DGI Gateway", GW, "key_fingerprint")
    res = approve_gateway(GW, fp)
    frappe.db.commit()
    print("APPROVED=" + res["status"] + " FP=" + fp)


def new_invoice():
    frappe.set_user("Administrator")
    si = fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 2, 10000), ("DGI-A-EAU", 1, 1500)],
                         template="DGI TVA HT", dgi_point_of_sale=PVF)
    from dgi_compliance.edef.tasks import normalize_sales_invoice
    res = normalize_sales_invoice(si.name)
    frappe.db.commit()
    print("INVOICE=" + si.name + " STAGE=" + str(res.get("stage")))


def invoice_state(name):
    si = frappe.get_doc("Sales Invoice", name)
    op = frappe.db.get_value("DGI Fiscal Operation", si.custom_dgi_operation, ["status", "gateway", "modified_by"], as_dict=True)
    print("STATE=" + frappe.as_json({"status": si.custom_dgi_status, "code_def": si.custom_dgi_code_def,
                                     "nim": si.custom_dgi_nim, "qr": si.custom_dgi_qr_code, "operation": op},
                                    indent=None))
