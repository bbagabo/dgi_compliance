"""Tests v5.4.1 - corrections de la revue du 11/11/2026 (passerelle, separation des taches, champs fiscaux).

Run on a TEST site:
    bench --site <site> run-tests --module dgi_compliance.tests.test_revue_541

V01-V04  fields written by the SFE on Sales Invoice are never taken from a form or an API call
V05-V06  system fields of DGI Gateway / DGI MCF Device (enrolment, approval, readings)
V07      the proposer of an MCF is the person who supplied the reading that is validated
V08      'Mise en service' is not a way around the two-person rule
V09      a refused enrolment code does not end the cashier's session
V10-V11  refusals are journalized even though the request is rolled back
V12-V13  cancelling and leasing an operation never cross (row lock)
V14      invoice inserted and submitted in one call (POS): sequential reference
The e-MCF and the MCF are simulators: no real fiscal invoice is emitted.
"""
import threading
import time
from unittest import mock

import frappe

from dgi_compliance.fiscal import approvals as A
from dgi_compliance.fiscal import operations as ops
from dgi_compliance.tests import fixtures as fx
from dgi_compliance.tests.test_deux_utilisateurs import (CAISSE, CAISSE_SEULE, GERANT, NID_U, PVF_U, _p256, _user,
                                                         as_user, set_rule)
from dgi_compliance.tests.test_hl2_mcf import NIF, PVF_L, _down, _upsert
from dgi_compliance.tests.test_sequence_pdv import CODE_Q, PVF_Q, SequenceBase

FAKE = {"custom_dgi_status": "Normalized", "custom_dgi_code_def": "FAUX-FAUX-FAUX-FAUX-FAUX-FAUX",
        "custom_dgi_nim": "XX00000000-1", "custom_dgi_counters": "1/1 FV", "custom_dgi_datetime": "11/11/2026 08:00:00",
        "custom_dgi_qr_code": "FAUX", "custom_dgi_uid": "faux-uid", "dgi_sequence_ref": "FAUX-2026-000001"}
PVF_MS = "V MISE EN SERVICE"


def events(code, **filters):
    return frappe.db.count("DGI Gateway Event", dict(filters, event_code=code))


class ReviewBase(SequenceBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _user(GERANT, ["DGI Fiscal Manager", "Accounts User", "Sales User"])
        _user(CAISSE, ["DGI Caisse MCF", "DGI Approbateur", "Accounts User", "Sales User"])
        _user(CAISSE_SEULE, ["DGI Caisse MCF", "Accounts User"])
        _upsert("DGI MCF Device", NID_U, {"nid": NID_U, "status": "Declare"})
        _upsert("DGI Point of Sale", PVF_U, {"pos_name": PVF_U, "company": fx.COMPANY, "is_active": 1,
                                             "is_sales_location": 0, "primary_channel": "MCF",
                                             "priority_reason": "Tests", "active_channel": "MCF",
                                             "mcf_device": NID_U, "fallback_policy": "Aucune",
                                             "nid_requirement": "Optional", "token_requirement": "Optional"})
        frappe.db.set_value("DGI MCF Device", NID_U, {"status": "Actif", "point_of_sale": PVF_U, "nif": NIF})
        frappe.db.commit()

    def setUp(self):
        super().setUp()
        set_rule(A.RULE_STATION, 1)
        frappe.db.commit()

    def tearDown(self):
        frappe.set_user("Administrator")
        set_rule(A.RULE_SWITCH, 0)
        frappe.db.commit()

    def station(self):
        gid = "POSTE-V-" + frappe.generate_hash(length=6).upper()
        frappe.get_doc({"doctype": "DGI Gateway", "gateway_id": gid, "kind": "Poste navigateur",
                        "point_of_sale": PVF_U}).insert(ignore_permissions=True)
        return gid

    def enrol(self, gid, by):
        from dgi_compliance.dgi_compliance.doctype.dgi_gateway.dgi_gateway import enroll_gateway, generate_enrollment_code
        code = as_user(GERANT, generate_enrollment_code, gid)["code"]
        return enroll_gateway(gid, code, _p256(), {"mid": NID_U + "-1", "nif": NIF}, {"hostname": "caisse"},
                              enrolled_by=by)["fingerprint"]


# =========================================================================== Sales Invoice
class TestInvoiceSystemFields(ReviewBase):
    def test_V01_forged_security_elements_on_a_draft_are_ignored_and_do_not_open_the_gate(self):
        si = self.invoice(pvf=PVF_Q)
        before = events("CHAMPS_FISCAUX_IGNORES")
        doc = self.reload(si)
        doc.update(FAKE)
        doc.save()
        stored = frappe.db.get_value("Sales Invoice", si.name, list(FAKE), as_dict=True)
        self.assertEqual(stored.custom_dgi_status, "Pending")
        for f in FAKE:
            if f != "custom_dgi_status":
                self.assertFalse(stored[f], f)
        self.assertEqual(events("CHAMPS_FISCAUX_IGNORES"), before + 1)
        # the same values sent WITH the submission (frappe.client.submit) while the e-MCF is down: no posting
        frappe.db.commit()
        doc = self.reload(si)
        doc.update(FAKE)
        with mock.patch("dgi_compliance.edef.client.requests.request", side_effect=_down), \
                mock.patch("dgi_compliance.edef.client.time.sleep"), self.assertRaises(frappe.ValidationError):
            doc.submit()
        frappe.db.rollback()
        self.assertEqual(frappe.db.get_value("Sales Invoice", si.name, ["docstatus", "custom_dgi_code_def"]), (0, None))

    def test_V02_new_invoice_starts_with_empty_system_fields(self):
        si = self.invoice(pvf=PVF_Q, **FAKE)
        stored = frappe.db.get_value("Sales Invoice", si.name, list(FAKE), as_dict=True)
        self.assertEqual(stored.custom_dgi_status, "Pending")
        self.assertFalse(stored.custom_dgi_code_def or stored.custom_dgi_uid or stored.dgi_sequence_ref)

    def test_V03_a_stale_form_never_erases_security_elements_received_meanwhile(self):
        si = self.invoice(pvf=PVF_Q)
        stale = self.reload(si)                                 # form opened while the invoice is Pending
        self.assertEqual(self.request(si)["stage"], "normalized")
        frappe.db.commit()
        real = frappe.db.get_value("Sales Invoice", si.name, ["custom_dgi_code_def", "custom_dgi_nim", "dgi_sequence_ref",
                                                             "custom_dgi_status"], as_dict=True)
        self.assertTrue(real.custom_dgi_code_def and real.dgi_sequence_ref)
        stale.remarks = "note ajoutee depuis un formulaire ouvert avant la normalisation"
        stale.save()
        after = frappe.db.get_value("Sales Invoice", si.name, ["custom_dgi_code_def", "custom_dgi_nim", "dgi_sequence_ref",
                                                              "custom_dgi_status"], as_dict=True)
        self.assertEqual(after, real)
        stale.reload()
        stale.submit()                                           # and the real elements open the gate
        self.assertEqual(frappe.db.get_value("Sales Invoice", si.name, "docstatus"), 1)

    def test_V04_after_submission_print_counters_and_reference_stay_as_stored(self):
        si = self.emcf_invoice()
        si.submit()
        frappe.db.set_value("Sales Invoice", si.name, {"custom_dgi_print_count": 3}, update_modified=False)
        doc = self.reload(si)
        doc.custom_dgi_print_count = 0                           # would print ORIGINAL again
        doc.custom_dgi_error = "x"
        doc.save()
        self.assertEqual(frappe.db.get_value("Sales Invoice", si.name, ["custom_dgi_print_count", "custom_dgi_error"]),
                         (3, None))
        doc = self.reload(si)
        doc.custom_dgi_code_def = "FAUX"
        with self.assertRaises(frappe.ValidationError):
            doc.save()

    def test_V18_desk_form_update_of_a_submitted_invoice_is_not_mistaken_for_a_forgery(self):
        """The desk form sends every field back, the dates as text (custom_dgi_normalized_on...)."""
        from frappe.desk.form.save import savedocs
        si = self.emcf_invoice()
        si.submit()
        frappe.db.set_value("Sales Invoice", si.name, {"custom_dgi_print_count": 2,
                                                       "custom_dgi_first_printed_on": "2026-11-11 08:00:00",
                                                       "custom_dgi_last_printed_on": "2026-11-11 08:05:00"},
                            update_modified=False)
        frappe.db.commit()
        real = frappe.db.get_value("Sales Invoice", si.name, ["custom_dgi_code_def", "custom_dgi_normalized_on",
                                                             "dgi_sequence_ref", "custom_dgi_print_count"])
        d = frappe.parse_json(frappe.get_doc("Sales Invoice", si.name).as_json())
        self.assertIsInstance(d["custom_dgi_normalized_on"], str)
        d["po_no"] = "BC-2026-0457"                                          # allowed after submission
        savedocs(frappe.as_json(d), "Update")
        self.assertEqual(frappe.db.get_value("Sales Invoice", si.name, "po_no"), "BC-2026-0457")
        self.assertEqual(frappe.db.get_value("Sales Invoice", si.name, ["custom_dgi_code_def", "custom_dgi_normalized_on",
                                                                        "dgi_sequence_ref", "custom_dgi_print_count"]), real)
        d = frappe.parse_json(frappe.get_doc("Sales Invoice", si.name).as_json())
        d["custom_dgi_normalized_on"] = "2020-01-01 00:00:00"
        with self.assertRaises(frappe.ValidationError):
            savedocs(frappe.as_json(d), "Update")
        frappe.db.rollback()

    def test_V14_invoice_inserted_and_submitted_in_one_call_gets_its_reference(self):
        from dgi_compliance.tests.test_sequence_pdv import YEAR, last
        before = last(CODE_Q)
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 1, 10000)], save=False, dgi_point_of_sale=PVF_Q)
        si.submit()
        frappe.db.commit()                                       # end of the request
        row = frappe.db.get_value("Sales Invoice", si.name, ["docstatus", "custom_dgi_status", "dgi_sequence_ref"])
        self.assertEqual(row, (1, "Normalized", f"{CODE_Q}-{YEAR}-{before + 1:06d}"))


# =========================================================================== DGI Gateway / DGI MCF Device
class TestSystemFieldsOfGatewayAndDevice(ReviewBase):
    def test_V05_gateway_fields_are_written_by_the_actions_only(self):
        gid = "POSTE-V-" + frappe.generate_hash(length=6).upper()
        as_user(GERANT, lambda: frappe.get_doc({
            "doctype": "DGI Gateway", "gateway_id": gid, "kind": "Service (poste dedie)", "point_of_sale": PVF_U,
            "status": "Active", "public_key": "QUJD", "key_fingerprint": "AAAA", "key_algorithm": "Ed25519",
            "api_user": GERANT, "enrolled_by": CAISSE, "approved_by": CAISSE}).insert())
        row = frappe.db.get_value("DGI Gateway", gid, ["status", "public_key", "api_user", "enrolled_by", "approved_by"])
        self.assertEqual(row, ("Nouvelle", None, None, None, None))        # born 'Nouvelle', without key
        pend = self.station()
        fp = self.enrol(pend, by=GERANT)
        before = events("MODIFICATION_REFUSEE", gateway=pend)

        def forge(field, value):
            d = frappe.get_doc("DGI Gateway", pend)
            d.set(field, value)
            d.save()
        for field, value in (("enrolled_by", CAISSE), ("public_key", "QUJD"), ("key_fingerprint", "AAAA-AAAA"),
                             ("api_user", GERANT), ("kind", "Service (poste dedie)")):
            with self.assertRaises(frappe.PermissionError, msg=field):
                as_user(GERANT, forge, field, value)
            frappe.db.rollback()
        self.assertEqual(events("MODIFICATION_REFUSEE", gateway=pend), before + 5)       # journalized, committed
        self.assertEqual(frappe.db.get_value("DGI Gateway", pend, ["enrolled_by", "key_fingerprint", "status"]),
                         (GERANT, fp, "A approuver"))
        as_user(GERANT, forge, "notes", "poste de la caisse 2")              # the editable fields still are
        as_user(GERANT, forge, "baudrate", 57600)
        self.assertEqual(frappe.db.get_value("DGI Gateway", pend, ["notes", "baudrate"]), ("poste de la caisse 2", 57600))
        from dgi_compliance.dgi_compliance.doctype.dgi_gateway.dgi_gateway import approve_gateway
        with self.assertRaises(frappe.ValidationError):                    # so the manager still cannot self-approve
            as_user(GERANT, approve_gateway, pend, fp)
        as_user(CAISSE, approve_gateway, pend, fp)
        self.assertEqual(frappe.db.get_value("DGI Gateway", pend, "status"), "Active")

    def test_V16_desk_form_of_an_active_poste_still_saves_after_a_heartbeat(self):
        """The desk form sends every field back, dates as text, and a heartbeat (written without changing
        'modified') makes an open form stale: neither is a forgery."""
        from frappe.desk.form.save import savedocs
        from dgi_compliance.dgi_compliance.doctype.dgi_gateway.dgi_gateway import approve_gateway
        gid = self.station()
        fp = self.enrol(gid, by=GERANT)
        as_user(CAISSE, approve_gateway, gid, fp)
        frappe.db.commit()
        form = frappe.get_doc("DGI Gateway", gid).as_json()                  # the form as the browser holds it
        frappe.db.set_value("DGI Gateway", gid, {"last_seen": "2026-11-11 09:15:00", "last_ip": "10.0.0.9",
                                                 "gateway_version": "web-9.9", "offline_alert": 0},
                            update_modified=False)                           # heartbeat meanwhile
        frappe.db.commit()
        before = events("MODIFICATION_REFUSEE", gateway=gid)
        d = frappe.parse_json(form)
        d["notes"] = "caisse 1, port USB de gauche"
        frappe.set_user(GERANT)
        try:
            savedocs(frappe.as_json(d), "Save")
        finally:
            frappe.set_user("Administrator")
        row = frappe.db.get_value("DGI Gateway", gid, ["notes", "last_ip", "gateway_version", "status"])
        self.assertEqual(row, ("caisse 1, port USB de gauche", "10.0.0.9", "web-9.9", "Active"))
        self.assertEqual(str(frappe.db.get_value("DGI Gateway", gid, "last_seen"))[:19], "2026-11-11 09:15:00")
        self.assertEqual(events("MODIFICATION_REFUSEE", gateway=gid), before)
        d = frappe.parse_json(frappe.get_doc("DGI Gateway", gid).as_json())  # a real forgery through the form
        d["approved_by"] = GERANT
        frappe.set_user(GERANT)
        try:
            with self.assertRaises(frappe.PermissionError):
                savedocs(frappe.as_json(d), "Save")
        finally:
            frappe.set_user("Administrator")
        frappe.db.rollback()
        self.assertEqual(frappe.db.get_value("DGI Gateway", gid, "approved_by"), CAISSE)

    def test_V06_device_readings_and_validation_fields_are_written_by_the_sfe_only(self):
        nid = "CC300V" + frappe.generate_hash(length=4).upper()
        as_user(GERANT, lambda: frappe.get_doc({"doctype": "DGI MCF Device", "nid": nid, "status": "Declare",
                                                "nif": NIF, "last_mid": nid + "-1", "discovered_by": CAISSE,
                                                "validated_mid": nid + "-1", "last_tc": 99}).insert())
        row = frappe.db.get_value("DGI MCF Device", nid, ["nif", "last_mid", "discovered_by", "validated_mid", "last_tc"])
        self.assertEqual(row, (None, None, None, None, 0))                   # declared by hand = never read
        from dgi_compliance.fiscal.registry import record_observation, validate_device
        record_observation({"mid": nid + "-1", "nif": NIF}, GERANT)

        def forge():
            d = frappe.get_doc("DGI MCF Device", nid)
            d.discovered_by, d.nif, d.notes = CAISSE, "Z9999999Z", "note"
            d.save()
        as_user(GERANT, forge)
        self.assertEqual(frappe.db.get_value("DGI MCF Device", nid, ["discovered_by", "nif", "notes"]),
                         (GERANT, NIF, "note"))                               # restored, the note is kept
        with self.assertRaises(frappe.ValidationError):                    # he proposed it: he does not validate it
            as_user(GERANT, validate_device, nid, PVF_Q, "ecarts acceptes pour le test")
        frappe.db.rollback()

    def test_V07_the_proposer_is_the_last_person_who_supplied_the_reading(self):
        from dgi_compliance.fiscal.mcf_status import record_desk_test
        from dgi_compliance.fiscal.registry import record_observation, validate_device
        nid = "CC300W" + frappe.generate_hash(length=4).upper()
        pvf = "V VALIDATION " + nid
        _upsert("DGI Point of Sale", pvf, {"pos_name": pvf, "company": fx.COMPANY, "is_active": 1,
                                           "is_sales_location": 0, "nid_requirement": "Optional",
                                           "token_requirement": "Optional"})
        name = frappe.db.get_value("Company", fx.COMPANY, "company_name")
        record_observation({"mid": nid + "-1", "nif": NIF, "taxpayer_name": name}, CAISSE_SEULE)   # the cashier reads
        self.assertEqual(frappe.db.get_value("DGI MCF Device", nid, "discovered_by"), CAISSE_SEULE)
        # the manager sends another reading: he becomes the proposer and cannot validate his own values
        record_observation({"mid": nid + "-2", "nif": NIF, "taxpayer_name": name}, GERANT)
        self.assertEqual(frappe.db.get_value("DGI MCF Device", nid, ["discovered_by", "last_mid"]), (GERANT, nid + "-2"))
        with self.assertRaises(frappe.ValidationError):
            as_user(GERANT, validate_device, nid, pvf)
        frappe.db.rollback()
        # a connection test by the future validator does not replace the reading awaiting validation
        as_user(CAISSE, record_desk_test, {"mid": nid + "-7", "nif": NIF, "tc": "1"}, nid, "test")
        self.assertEqual(frappe.db.get_value("DGI MCF Device", nid, "last_mid"), nid + "-2")
        as_user(CAISSE, validate_device, nid, pvf)                          # the approver validates the manager's reading
        self.assertEqual(frappe.db.get_value("DGI MCF Device", nid, ["status", "validated_by", "validated_mid"]),
                         ("Actif", CAISSE, nid + "-2"))


# =========================================================================== approvals
class TestApprovalsAndJournal(ReviewBase):
    def test_V08_mise_en_service_is_exempt_from_two_people_only_for_a_real_commissioning(self):
        set_rule(A.RULE_SWITCH, 1)

        def request(pvf, to="e-MCF"):
            return frappe.get_doc({"doctype": "DGI Channel Switch", "point_of_sale": pvf, "to_channel": to,
                                   "reason": "Mise en service"}).insert().name

        def approve(name):
            frappe.get_doc("DGI Channel Switch", name).submit()
        # 1. a point de vente that already normalizes invoices: 'Mise en service' is an ordinary switch
        self.emcf_invoice(pvf=PVF_Q)
        frappe.db.set_value("DGI Point of Sale", PVF_Q, {"switch_allowed": 0, "active_channel": "MCF"})
        frappe.clear_document_cache("DGI Point of Sale", PVF_Q)
        frappe.db.commit()
        try:
            name = as_user(GERANT, request, PVF_Q)
            frappe.db.commit()
            with self.assertRaises(frappe.ValidationError) as cm:           # the requester alone: refused
                as_user(GERANT, approve, name)
            self.assertIn("deux personnes", str(cm.exception))
            frappe.db.rollback()
            self.assertEqual(frappe.db.get_value("DGI Channel Switch", name, "docstatus"), 0)
            as_user(CAISSE, approve, name)                                   # a second person: accepted
            self.assertEqual(frappe.db.get_value("DGI Channel Switch", name, ["docstatus", "approved_by"]), (1, CAISSE))
        finally:
            frappe.db.set_value("DGI Point of Sale", PVF_Q, {"active_channel": "e-MCF", "forced_switch": None})
            frappe.clear_document_cache("DGI Point of Sale", PVF_Q)
            frappe.db.commit()
        # 2. a real commissioning (first switch, no normalized invoice): one person is enough
        pvf = PVF_MS + " " + frappe.generate_hash(length=4).upper()
        _upsert("DGI Point of Sale", pvf, {"pos_name": pvf, "company": fx.COMPANY, "is_active": 1,
                                           "is_sales_location": 0, "token": "TOKEN-MS", "active_channel": "MCF",
                                           "primary_channel": "MCF", "priority_reason": "Tests",
                                           "nid_requirement": "Optional", "token_requirement": "Optional"})
        first = as_user(GERANT, request, pvf)
        as_user(GERANT, approve, first)
        self.assertEqual(frappe.db.get_value("DGI Point of Sale", pvf, "active_channel"), "e-MCF")
        # 3. ... but only once: the next 'Mise en service' of the same point de vente needs a second person
        second = as_user(GERANT, request, pvf, "MCF")
        with self.assertRaises(frappe.ValidationError) as cm:
            as_user(GERANT, approve, second)
        self.assertIn("deux personnes", str(cm.exception))
        frappe.db.rollback()

    def test_V09_refused_enrolment_code_is_not_an_authentication_error_for_the_cashier(self):
        from dgi_compliance.dgi_compliance.doctype.dgi_gateway.dgi_gateway import EnrollmentRefused, enroll_gateway
        from dgi_compliance.gateway.station import enroll_station
        gid = self.station()
        self.assertTrue(issubclass(EnrollmentRefused, frappe.AuthenticationError))
        self.assertIsNot(EnrollmentRefused, frappe.AuthenticationError)      # Frappe clears the cookies for the exact class
        with self.assertRaises(EnrollmentRefused):                         # service gateway: still HTTP 401
            enroll_gateway(gid, "AAAA-BBBB-CCCC", _p256(), {}, {})
        before = events("ENROLEMENT_REFUSE", gateway=gid)
        try:
            as_user(CAISSE, enroll_station, gid, "AAAA-BBBB-CCCC", _p256(), {"mid": NID_U + "-1", "nif": NIF}, {})
            self.fail("code refuse attendu")
        except frappe.AuthenticationError:
            self.fail("un code refuse ne doit pas terminer la session (HTTP 401)")
        except frappe.ValidationError as e:
            self.assertIn("Code d'enrolement refuse", str(e))
        frappe.db.rollback()
        self.assertEqual(events("ENROLEMENT_REFUSE", gateway=gid), before + 1)

    def test_V10_refused_approvals_stay_in_the_journal_after_the_rollback(self):
        from dgi_compliance.dgi_compliance.doctype.dgi_gateway.dgi_gateway import approve_gateway
        gid = self.station()
        fp = self.enrol(gid, by=GERANT)
        frappe.db.commit()
        before = events("APPROBATION_REFUSEE", gateway=gid)
        with self.assertRaises(frappe.ValidationError):                    # wrong fingerprint
            as_user(CAISSE, approve_gateway, gid, "0000-0000-0000-0000-0000")
        frappe.db.rollback()                                                # what the web request does
        self.assertEqual(events("APPROBATION_REFUSEE", gateway=gid), before + 1)
        with self.assertRaises(frappe.ValidationError):                    # two-person rule
            as_user(GERANT, approve_gateway, gid, fp)
        frappe.db.rollback()
        self.assertEqual(events("APPROBATION_REFUSEE", gateway=gid), before + 2)
        self.assertEqual(frappe.db.get_value("DGI Gateway", gid, "status"), "A approuver")

    def test_V15_service_gateway_is_not_approved_by_the_person_who_issued_its_code(self):
        from dgi_compliance.dgi_compliance.doctype.dgi_gateway.dgi_gateway import (approve_gateway, enroll_gateway,
                                                                                   generate_enrollment_code)
        from dgi_gateway.identity import Identity
        import os
        gid = "SVC-V-" + frappe.generate_hash(length=6).upper()
        frappe.get_doc({"doctype": "DGI Gateway", "gateway_id": gid, "kind": "Service (poste dedie)",
                        "point_of_sale": PVF_U}).insert(ignore_permissions=True)
        code = as_user(GERANT, generate_enrollment_code, gid)["code"]
        ident = Identity.create(gid, os.path.join(self.tmp, gid + ".key"), overwrite=True)
        fp = enroll_gateway(gid, code, ident.public_key_b64, {"mid": NID_U + "-1", "nif": NIF}, {})["fingerprint"]
        self.assertEqual(frappe.db.get_value("DGI Gateway", gid, ["enrolled_by", "code_issued_by"]), (None, GERANT))
        frappe.db.commit()
        with self.assertRaises(frappe.ValidationError):                    # he issued the code: not his approval
            as_user(GERANT, approve_gateway, gid, fp)
        frappe.db.rollback()
        as_user(CAISSE, approve_gateway, gid, fp)                           # the approver compares the fingerprint
        self.assertEqual(frappe.db.get_value("DGI Gateway", gid, ["status", "approved_by"]), ("Active", CAISSE))

    def test_V17_refused_gateway_requests_are_journalized_once_per_window(self):
        from dgi_compliance.gateway import auth
        gid = self.station()
        base = f"dgi_gw_reject|{gid}|SIGNATURE_REFUSEE"
        gate = frappe.cache.make_key(base, shared=True)
        before = events("SIGNATURE_REFUSEE", gateway=gid)
        for _i in range(6):                                                  # a poste retrying, or a flood
            with self.assertRaises(auth.GatewayAuthError):
                auth._reject("SIGNATURE_REFUSEE", "Signature de passerelle invalide.", gid)
        self.assertEqual(events("SIGNATURE_REFUSEE", gateway=gid), before + 1)
        with self.assertRaises(auth.GatewayAuthError):                     # another kind of refusal: its own line
            auth._reject("HORLOGE", "Horodatage hors fenetre.", gid)
        self.assertEqual(events("HORLOGE", gateway=gid), 1)
        frappe.cache.delete(gate)                                            # the 10 minutes have passed
        with self.assertRaises(auth.GatewayAuthError):
            auth._reject("SIGNATURE_REFUSEE", "Signature de passerelle invalide.", gid)
        last = frappe.get_all("DGI Gateway Event", filters={"gateway": gid, "event_code": "SIGNATURE_REFUSEE"},
                              fields=["message", "details"], order_by="creation desc", limit=1)[0]
        self.assertEqual(events("SIGNATURE_REFUSEE", gateway=gid), before + 2)
        self.assertIn("5 refus identiques", last.message)
        self.assertEqual(frappe.parse_json(last.details).get("suppressed"), 5)

    def test_V11_result_for_an_operation_never_leased_is_journalized_and_refused(self):
        L = self.sites[PVF_L]
        si = self.invoice(pvf=PVF_L)
        self.request(si)
        op = self.op_of(si)
        frappe.db.commit()
        before = events("RESULTAT_SANS_BAIL")
        forged = {"operation_id": op.operation_id, "outcome": "normalized", "lease_token": "x",
                  "data": {"mid": "X-1", "sig": "A" * 24, "fn": si.name, "nif": NIF, "dt": "20261111080000"}}
        with self.assertRaises(frappe.PermissionError):
            L.send(forged)
        frappe.db.rollback()
        self.assertEqual(events("RESULTAT_SANS_BAIL"), before + 1)
        self.assertEqual(frappe.db.get_value("DGI Fiscal Operation", op.name, "status"), "Queued")
        self.assertNotEqual(frappe.db.get_value("Sales Invoice", si.name, "custom_dgi_status"), "Normalized")


# =========================================================================== operations: cancel / lease
def _hold_and_cancel(site, sites_path, op_name, hold, started, out):
    frappe.init(site=site, sites_path=sites_path)
    frappe.connect()
    try:
        frappe.set_user("Administrator")
        res = ops.withdraw_queued(op_name, "annulation concurrente (test)")   # row lock taken here
        started.set()
        time.sleep(hold)
        frappe.db.commit()
        out["cancelled"] = bool(res)
    except Exception as e:                                                   # noqa: BLE001
        frappe.db.rollback()
        out["cancelled"] = repr(e)
    finally:
        frappe.destroy()


class TestCancelAndLease(ReviewBase):
    def test_V12_a_leased_operation_is_never_cancelled(self):
        from dgi_compliance.fiscal.api import cancel_queued
        L = self.sites[PVF_L]
        si = self.invoice(pvf=PVF_L)
        self.request(si)
        op = self.op_of(si)
        msg = L.lease()
        self.assertEqual(msg.get("operation_id"), op.operation_id)
        self.assertIsNone(ops.withdraw_queued(op.name, "trop tard"))
        with self.assertRaises(frappe.ValidationError):
            cancel_queued(op.name)
        self.assertEqual(ops.cancel_queued_for_invoice(si.name, "trop tard"), 0)
        self.assertEqual(frappe.db.get_value("DGI Fiscal Operation", op.name, "status"), "Leased")
        res = L.executor.execute(msg)
        L.send(res)
        frappe.db.commit()
        self.assertEqual(self.reload(si).custom_dgi_status, "Normalized")

    def test_V13_cancel_and_lease_crossing_never_gives_a_cancelled_operation_to_the_gateway(self):
        L = self.sites[PVF_L]
        si = self.invoice(pvf=PVF_L)
        self.request(si)
        op = self.op_of(si)
        frappe.db.commit()
        out, started = {}, threading.Event()
        t = threading.Thread(target=_hold_and_cancel, args=(frappe.local.site, frappe.local.sites_path, op.name, 1.5,
                                                            started, out))
        t.start()
        self.assertTrue(started.wait(20))
        t0 = time.time()
        msg = L.lease()                            # waits for the row lock, then reads the status again
        waited = time.time() - t0
        frappe.db.commit()
        t.join(60)
        self.assertEqual(out.get("cancelled"), True, out)
        self.assertGreater(waited, 0.8)                                       # it really waited for the other request
        self.assertFalse(msg.get("operation_id"), msg)                        # nothing given to the gateway
        self.assertEqual(frappe.db.get_value("DGI Fiscal Operation", op.name, "status"), "Cancelled")
