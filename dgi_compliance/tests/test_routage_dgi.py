"""Integration tests v5.3.0 - routage DGI, secours dans les deux sens, decouverte du MCF, journal unique.

Livrable B (architecture detaillee) and livrable D (plan de test, essais R / P / S / D / M).
Run on a TEST site:
    bench --site <site> run-tests --module dgi_compliance.tests.test_routage_dgi

Reuses the HL2 fixtures (points de vente Lemba, Ngaliema with MCF prioritaire; Kintambo e-MCF
prioritaire with its MCF; e-MCF simulator; protocol simulators of the MCF behind real gateways).
No real fiscal invoice is emitted: the e-MCF and the MCF are simulators.
"""
import json
from unittest import mock

import frappe
from frappe.utils import add_to_date, now_datetime

from dgi_compliance.fiscal import fallback as FB
from dgi_compliance.fiscal import operations as ops
from dgi_compliance.fiscal import router as R
from dgi_compliance.tests import fixtures as fx
from dgi_compliance.tests.test_hl2_mcf import (GW_K, HL2Base, LINES, NID_K, NIF, PVF_E, PVF_K, PVF_L, PVF_N,
                                               _down)

CASHIER = "caisse.routage@test.local"
WH_GROUP, WH_LEMBA, WH_NGAL = "R Groupe", "R Depot Lemba", "R Depot Ngaliema"
POS_LEMBA = "R POS LEMBA"
BRANCH = "R AGENCE LEMBA"
PVF_D = "R DECOUVERTE"
GW_D = "GW-R-DECOUV"


def W(name):
    """Full warehouse name, whatever the abbreviation of the test company (v5.3.1: no longer 'DTS' hard-coded)."""
    return f"{name} - {fx.abbr()}"


def _wh(name, parent=None, is_group=0):
    full = W(name)
    if not frappe.db.exists("Warehouse", full):
        frappe.get_doc({"doctype": "Warehouse", "warehouse_name": name, "company": fx.COMPANY, "is_group": is_group,
                        "parent_warehouse": parent or W("All Warehouses")}).insert(ignore_permissions=True)
    return full


def set_settings(**values):
    for k, v in values.items():
        frappe.db.set_single_value("DGI Compliance Settings", k, v)
    frappe.clear_document_cache("DGI Compliance Settings", "DGI Compliance Settings")
    frappe.clear_cache(doctype="DGI Compliance Settings")


def set_pvf(name, **values):
    doc = frappe.get_doc("DGI Point of Sale", name)
    doc.update(values)
    doc.save(ignore_permissions=True)
    return doc


def count(code, **filters):
    return frappe.db.count("DGI Gateway Event", dict(filters, event_code=code))


class RoutageBase(HL2Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not frappe.db.exists("User", CASHIER):
            u = frappe.get_doc({"doctype": "User", "email": CASHIER, "first_name": "Caisse", "send_welcome_email": 0,
                                "roles": [{"role": "DGI Caisse MCF"}, {"role": "Accounts User"}]})
            u.insert(ignore_permissions=True)
        group = _wh(WH_GROUP, is_group=1)
        _wh(WH_LEMBA, parent=group)
        _wh(WH_NGAL)
        frappe.db.set_value("Warehouse", group, "dgi_point_of_sale", PVF_L)
        frappe.db.set_value("Warehouse", W(WH_NGAL), "dgi_point_of_sale", PVF_N)
        if not frappe.db.exists("POS Profile", POS_LEMBA):
            p = frappe.new_doc("POS Profile")
            p.name = POS_LEMBA
            p.company, p.warehouse, p.currency = fx.COMPANY, W("Stores"), "CDF"
            p.dgi_point_of_sale = PVF_L
            p.disabled = 1          # never picked by ERPNext for the POS invoices of the other test modules
            p.db_insert()
        frappe.db.set_value("POS Profile", POS_LEMBA, "disabled", 1)
        if not frappe.db.exists("Branch", BRANCH):
            frappe.get_doc({"doctype": "Branch", "branch": BRANCH}).insert(ignore_permissions=True)
        set_settings(use_warehouse_mapping=0, warehouse_mismatch_action="Signaler", primary_channel="e-MCF",
                     mcf_fallback_after_s=60, default_point_of_sale=fx.HL1_PVF)
        frappe.db.commit()

    def setUp(self):
        super().setUp()
        frappe.set_user("Administrator")
        set_settings(use_warehouse_mapping=0, warehouse_mismatch_action="Signaler", primary_channel="e-MCF")
        frappe.db.set_value("DGI Point of Sale", PVF_K, {"primary_channel": "e-MCF", "priority_reason": "Tests HL2",
                                                        "active_channel": "e-MCF", "forced_switch": None,
                                                        "fallback_policy": "Automatique", "token_check": "Non verifie",
                                                        "nim": None})
        frappe.db.set_value("DGI MCF Device", NID_K, {"status": "Actif", "point_of_sale": PVF_K, "validated_mid": None})
        frappe.clear_document_cache("DGI Point of Sale", PVF_K)
        frappe.db.commit()

    def draft(self, pvf=None, warehouses=(), **extra):
        """In-memory invoice (not saved): sources only."""
        si = fx.make_invoice("CLIENT PM SARL", LINES, template="DGI TVA HT", save=False, **extra)
        if pvf:
            si.dgi_point_of_sale = pvf
        for i, wh in enumerate(warehouses):
            si.items[i].warehouse = wh
        for it in si.items[len(warehouses):]:
            it.warehouse = None
        return si


# =========================================================================== section 4 : point de vente
class TestResolution(RoutageBase):
    def test_R01_emission_source_designates_the_point_of_sale(self):
        res = R.resolve(self.draft(PVF_L))
        self.assertEqual((res["pvf"], res["rule"], res["blocked"]), (PVF_L, R.EMISSION, None))
        si = self.draft(pos_profile=POS_LEMBA)
        self.assertEqual(R.resolve(si)["pvf"], PVF_L)
        self.assertEqual([s["source"] for s in R.resolve(si)["sources"]], ["pos_profile"])

    def test_R02_contradictory_sources_block_with_every_value_shown(self):
        si = self.draft(PVF_N, pos_profile=POS_LEMBA)
        res = R.resolve(si)
        self.assertEqual(res["blocked"]["code"], R.CONFLIT_SOURCES)
        self.assertIn(PVF_N, res["blocked"]["message"])
        self.assertIn(PVF_L, res["blocked"]["message"])
        with self.assertRaises(R.RoutingError) as cm:
            R.route(si)
        self.assertEqual(cm.exception.code, R.CONFLIT_SOURCES)

    def test_R03_arbitration_is_reserved_and_traced(self):
        from dgi_compliance.fiscal.journal import validate_routing_fields
        si = self.draft(PVF_N, pos_profile=POS_LEMBA)
        si.dgi_pos_arbitration = "Livraison depuis Ngaliema, facture du siege"
        frappe.set_user(CASHIER)
        try:
            with self.assertRaises(frappe.PermissionError):
                validate_routing_fields(si)
        finally:
            frappe.set_user("Administrator")
        validate_routing_fields(si)
        self.assertEqual(si.dgi_pos_arbitrated_by, "Administrator")
        res = R.resolve(si)
        self.assertEqual((res["pvf"], res["rule"]), (PVF_N, R.ARBITRAGE))
        self.assertTrue(any("Administrator" in w for w in res["warnings"]))

    def test_R04_warehouses_decide_only_without_emission_source_group_inherits(self):
        si = self.draft(warehouses=[W(WH_LEMBA)])
        self.assertEqual(R.resolve(si)["rule"], R.DEFAUT_PARAMETRES)          # option off: warehouses ignored
        set_settings(use_warehouse_mapping=1)
        res = R.resolve(si)
        self.assertEqual((res["pvf"], res["rule"]), (PVF_L, R.ENTREPOT))       # child of the mapped group

    def test_R05_warehouses_of_two_points_of_sale_block(self):
        set_settings(use_warehouse_mapping=1)
        res = R.resolve(self.draft(warehouses=[W(WH_LEMBA), W(WH_NGAL)]))
        self.assertEqual(res["blocked"]["code"], R.CONFLIT_ENTREPOTS)

    def test_R06_centralized_invoice_signalled_or_blocked(self):
        set_settings(use_warehouse_mapping=1)
        si = self.draft(PVF_L, warehouses=[W(WH_NGAL)])
        res = R.resolve(si)
        self.assertEqual((res["pvf"], res["rule"]), (PVF_L, R.EMISSION))
        self.assertTrue(res["warnings"][0].startswith(R.EMISSION_CENTRALISEE))
        set_settings(warehouse_mismatch_action="Bloquer")
        self.assertEqual(R.resolve(si)["blocked"]["code"], R.EMISSION_CENTRALISEE)

    def test_R07_no_source_default_then_blocked(self):
        si = self.draft()
        self.assertEqual((R.resolve(si)["pvf"], R.resolve(si)["rule"]), (fx.HL1_PVF, R.DEFAUT_PARAMETRES))
        set_settings(default_point_of_sale=None)
        try:
            self.assertEqual(R.resolve(si)["blocked"]["code"], R.AUCUNE_SOURCE)
        finally:
            set_settings(default_point_of_sale=fx.HL1_PVF)

    def test_R08_branch_is_unique_per_company(self):
        set_pvf(PVF_L, branch=BRANCH)
        try:
            si = self.draft()
            si.branch = BRANCH
            res = R.resolve(si)
            self.assertEqual((res["pvf"], [s["source"] for s in res["sources"]]), (PVF_L, ["agence"]))
            with self.assertRaises(frappe.ValidationError):
                set_pvf(PVF_N, branch=BRANCH)
        finally:
            frappe.db.set_value("DGI Point of Sale", PVF_L, "branch", None)
            frappe.db.set_value("DGI Point of Sale", PVF_N, "branch", None)

    def test_R09_point_of_sale_of_another_company_is_refused(self):
        frappe.db.set_value("DGI Point of Sale", PVF_N, "company", "AUTRE SOCIETE")
        try:
            self.assertEqual(R.resolve(self.draft(PVF_N))["blocked"]["code"], R.SOCIETE_DIFFERENTE)
        finally:
            frappe.db.set_value("DGI Point of Sale", PVF_N, "company", fx.COMPANY)

    def test_R10_only_fiscal_managers_map_warehouses_and_pos_profiles(self):
        wh = frappe.get_doc("Warehouse", W(WH_NGAL))
        wh.dgi_point_of_sale = PVF_L
        frappe.set_user(CASHIER)
        try:
            from dgi_compliance.fiscal.mapping import validate_mapping
            with self.assertRaises(frappe.PermissionError):
                validate_mapping(wh)
        finally:
            frappe.set_user("Administrator")


# =========================================================================== section 5 : canal
class TestChannelPriority(RoutageBase):
    def test_P01_override_needs_a_reason_and_a_fiscal_manager(self):
        pvf = frappe.get_doc("DGI Point of Sale", PVF_E)
        pvf.primary_channel, pvf.priority_reason = "MCF", ""
        with self.assertRaises(frappe.ValidationError):
            pvf.save()
        pvf.reload()
        pvf.fallback_policy = "Aucune"
        frappe.set_user(CASHIER)
        try:
            with self.assertRaises(frappe.PermissionError):
                pvf.save(ignore_permissions=True)
        finally:
            frappe.set_user("Administrator")

    def test_P02_general_priority_mcf_and_not_equipped_point_of_sale(self):
        before = count("PRIORITE_MODIFIEE")
        s = frappe.get_single("DGI Compliance Settings")
        s.primary_channel = "MCF"
        s.flags.ignore_mandatory = True
        s.save(ignore_permissions=True)
        try:
            self.assertEqual(count("PRIORITE_MODIFIEE"), before + 1)
            for name in (PVF_K, PVF_E):
                frappe.db.set_value("DGI Point of Sale", name, {"primary_channel": None, "priority_reason": None})
                frappe.clear_document_cache("DGI Point of Sale", name)
            frappe.db.set_value("DGI Gateway", GW_K, "last_seen", now_datetime())
            r = R.route(self.draft(PVF_K))
            self.assertEqual((r.channel, r.rules[1]), ("MCF", R.PRIORITE_PARAMETRES))
            r = R.route(self.draft(PVF_E))                     # no MCF: the e-MCF, said in the decision
            self.assertEqual(r.channel, "e-MCF")
            self.assertIn(R.PRIORITAIRE_NON_EQUIPE, r.rules)
            self.assertIn("PRIORITAIRE_NON_EQUIPE", r.explanation())
        finally:
            s = frappe.get_single("DGI Compliance Settings")
            s.primary_channel = "e-MCF"                       # through save: the points de vente follow again
            s.flags.ignore_mandatory = True
            s.save(ignore_permissions=True)
            frappe.db.set_value("DGI Point of Sale", PVF_E, {"primary_channel": "e-MCF", "priority_reason": "Tests HL2"})

    def test_P03_manual_switch_imposes_its_channel_until_return(self):
        set_settings(switch_requires_two_people=0)
        frappe.db.set_value("DGI Gateway", GW_K, "last_seen", now_datetime())
        sw = frappe.get_doc({"doctype": "DGI Channel Switch", "point_of_sale": PVF_K, "to_channel": "MCF",
                             "reason": "Maintenance"}).insert()
        sw.submit()
        self.assertEqual(frappe.db.get_value("DGI Point of Sale", PVF_K, "forced_switch"), sw.name)
        r = R.route(self.draft(PVF_K))
        self.assertEqual(r.channel, "MCF")
        self.assertIn(f"{R.BASCULE_MANUELLE} {sw.name}", r.rules)
        back = frappe.get_doc({"doctype": "DGI Channel Switch", "point_of_sale": PVF_K, "to_channel": "MCF",
                               "reason": "Retour au canal principal"}).insert()
        self.assertEqual(back.to_channel, "e-MCF")               # the prioritized channel, whatever was typed
        back.submit()
        self.assertFalse(frappe.db.get_value("DGI Point of Sale", PVF_K, "forced_switch"))
        self.assertEqual(R.route(self.draft(PVF_K)).channel, "e-MCF")

    def test_P04_token_of_another_emcf_makes_the_emcf_unusable(self):
        from dgi_compliance.dgi_compliance.doctype.dgi_point_of_sale.dgi_point_of_sale import check_emcf_token
        if not frappe.db.exists("DGI EMCF", "RT01000001-1"):
            frappe.get_doc({"doctype": "DGI EMCF", "nim": "RT01000001-1", "shop_name": "TEST"}).insert()
        frappe.db.set_value("DGI Point of Sale", PVF_K, "nim", "RT01000001-1")
        frappe.clear_document_cache("DGI Point of Sale", PVF_K)
        out = check_emcf_token(PVF_K)                              # the simulator answers another NIM
        self.assertEqual(out["result"], "Different")
        self.assertEqual(frappe.db.get_value("DGI Point of Sale", PVF_K, "token_check"), "Different")
        self.assertTrue(count("NIM_JETON_DIFFERENT", point_of_sale=PVF_K))
        frappe.db.set_value("DGI Gateway", GW_K, "last_seen", now_datetime())
        r = R.route(self.draft(PVF_K))
        self.assertEqual(r.channel, "MCF")
        self.assertIn(R.PRIORITAIRE_NON_EQUIPE, r.rules)

    def test_P05_decision_is_stored_on_the_attempt_and_the_invoice(self):
        si = self.invoice(pvf=PVF_K)
        res = self.request(si)
        self.assertTrue(res.get("ok"), res)
        si = self.reload(si)
        self.assertEqual(si.custom_dgi_channel, "e-MCF")
        self.assertIn("Point de vente " + PVF_K, si.dgi_routing_summary)
        self.assertIn("Canal e-MCF", si.dgi_routing_summary)
        op = frappe.get_doc("DGI Fiscal Operation", si.custom_dgi_operation)
        self.assertEqual((op.channel, op.status, op.confirm_sent), ("e-MCF", "Normalized", 1))
        self.assertIn(R.EMISSION, op.routing_rule)
        self.assertIn(R.PRIORITE_POINT_DE_VENTE, op.routing_rule)
        self.assertEqual(json.loads(op.routing_sources)["pvf"], PVF_K)


# =========================================================================== secours dans les deux sens
class TestSymmetricFallback(RoutageBase):
    def mcf_first(self, online=True):
        frappe.db.set_value("DGI Point of Sale", PVF_K, {"primary_channel": "MCF", "priority_reason": "Tests secours",
                                                        "active_channel": "MCF"})
        frappe.clear_document_cache("DGI Point of Sale", PVF_K)
        frappe.db.set_value("DGI Gateway", GW_K, "last_seen",
                            now_datetime() if online else add_to_date(now_datetime(), minutes=-30))

    def test_S01_silent_station_sends_to_the_point_of_sale_emcf(self):
        self.mcf_first(online=False)
        before = count("SECOURS_EMCF", point_of_sale=PVF_K)
        si = self.invoice(pvf=PVF_K)
        res = self.request(si)
        self.assertTrue(res.get("ok"), res)
        si = self.reload(si)
        self.assertEqual((si.custom_dgi_status, si.custom_dgi_channel), ("Normalized", "e-MCF"))
        self.assertFalse(frappe.db.exists("DGI Fiscal Operation", {"sales_invoice": si.name, "channel": "MCF"}))
        self.assertEqual(count("SECOURS_EMCF", point_of_sale=PVF_K), before + 1)
        self.assertEqual(FB.state(PVF_K)["from"], "MCF")
        self.assertIn(R.SECOURS_AUTO, frappe.db.get_value("DGI Fiscal Operation", si.custom_dgi_operation, "routing_rule"))
        self.assertEqual(len(self.kintambo.sim.invoices), 0)

    def test_S02_queue_not_taken_goes_to_the_emcf_once(self):
        self.mcf_first(online=True)
        si = self.invoice(pvf=PVF_K)
        self.assertEqual(self.request(si)["stage"], "queued-mcf")
        op = self.op_of(si)
        old = add_to_date(now_datetime(), seconds=-120)
        frappe.db.set_value("DGI Fiscal Operation", op.name, "queued_at", old)
        frappe.db.sql("""update `tabDGI Fiscal Operation` set leased_at=%s
                         where mcf_device=%s and leased_at > %s""", (old, NID_K, old))   # earlier tests leased
        frappe.db.set_value("DGI Gateway", GW_K, "last_seen", add_to_date(now_datetime(), minutes=-30))
        with mock.patch("frappe.enqueue", side_effect=lambda method, **kw: FB.renormalize(kw["invoice"])):
            moved = FB.send_stale_mcf_queue_to_emcf()
        self.assertEqual(moved, 1)
        self.assertEqual(frappe.db.get_value("DGI Fiscal Operation", op.name, "status"), "Cancelled")
        si = self.reload(si)
        self.assertEqual((si.custom_dgi_status, si.custom_dgi_channel), ("Normalized", "e-MCF"))
        self.assertEqual(len(self.kintambo.sim.invoices), 0)            # never on the MCF
        self.assertEqual(FB.state(PVF_K)["to"], "e-MCF")

    def test_S03_never_from_an_operation_taken_by_the_station(self):
        self.mcf_first(online=True)
        si = self.invoice(pvf=PVF_K)
        self.request(si)
        msg = self.kintambo.lease()
        self.assertEqual(msg.get("fn"), si.name)
        old = add_to_date(now_datetime(), seconds=-600)
        op = self.op_of(si)
        frappe.db.set_value("DGI Fiscal Operation", op.name, {"queued_at": old, "leased_at": old})
        frappe.db.set_value("DGI Gateway", GW_K, "last_seen", add_to_date(now_datetime(), minutes=-30))
        self.assertEqual(FB.send_stale_mcf_queue_to_emcf(), 0)
        self.assertEqual(frappe.db.get_value("DGI Fiscal Operation", op.name, "status"), "Leased")
        self.assertFalse(frappe.db.exists("DGI Fiscal Operation", {"sales_invoice": si.name, "channel": "e-MCF"}))

    def test_S04_return_to_the_mcf_after_a_signal(self):
        self.mcf_first(online=True)
        frappe.cache.set(FB._key(PVF_K), json.dumps({"since": "2026-01-01 00:00:00", "reason": "test", "from": "MCF",
                                                      "to": "e-MCF"}))
        before = count("RETOUR_MCF", point_of_sale=PVF_K)
        ops.record_heartbeat(self.kintambo.doc, {"mid": NID_K + "-1", "nif": NIF, "flags": []})
        FB.probe_all()
        self.assertFalse(FB.is_active(PVF_K))
        self.assertEqual(count("RETOUR_MCF", point_of_sale=PVF_K), before + 1)
        si = self.invoice(pvf=PVF_K)
        self.assertEqual(self.request(si)["stage"], "queued-mcf")

    def test_S05_one_active_attempt_per_invoice_across_channels(self):
        for _i in range(3):
            self.emcf.inject("PUT", "/api/invoice", "timeout")
        with mock.patch("dgi_compliance.edef.client.time.sleep"):
            si = self.invoice(pvf=PVF_K)
            self.request(si)
        self.assertEqual(frappe.db.get_value("DGI Fiscal Operation", {"sales_invoice": si.name}, "status"), "Uncertain")
        FB.activate(PVF_K, "test", "e-MCF", "MCF")                 # the e-MCF goes down afterwards
        res = self.request(si)
        self.assertEqual(res["stage"], "operation-active")
        self.assertFalse(frappe.db.exists("DGI Fiscal Operation", {"sales_invoice": si.name, "channel": "MCF"}))

    def test_S06_general_token_is_never_a_fallback(self):
        frappe.db.set_value("DGI Gateway", self.sites[PVF_L].gateway_id, "last_seen",
                            add_to_date(now_datetime(), minutes=-30))
        calls = []
        with mock.patch("dgi_compliance.edef.client.requests.request",
                        side_effect=lambda *a, **k: calls.append(a) or _down()):
            si = self.invoice(pvf=PVF_L)
            self.assertEqual(self.request(si)["stage"], "queued-mcf")
        self.assertEqual(calls, [])


# =========================================================================== section 7 : decouverte
class TestDiscovery(RoutageBase):
    OBS = {"mid": "RD00000001-1", "nif": NIF, "taxpayer_name": "DGI TEST SARL",
           "taxpayer_address": "Boulevard du 30 juin, Kinshasa", "tc": "0", "tax_rates": {"A": 0.0, "B": 16.0}}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not frappe.db.exists("DGI Point of Sale", PVF_D):
            frappe.get_doc({"doctype": "DGI Point of Sale", "pos_name": PVF_D, "company": fx.COMPANY, "is_active": 1,
                            "is_sales_location": 0, "nid_requirement": "Optional", "token_requirement": "Optional",
                            "pos_address": "Boulevard du 30 juin", "pos_city": "Kinshasa"}).insert(ignore_permissions=True)
        # fresh discovery state at every run
        for nid in frappe.get_all("DGI MCF Device", filters={"name": ["like", "RD0000000%"]}, pluck="name"):
            frappe.db.delete("DGI MCF Device", nid)
        frappe.db.set_value("DGI Point of Sale", PVF_D, "mcf_device", None)
        if frappe.db.exists("DGI Gateway", GW_D):
            frappe.db.delete("DGI Gateway", GW_D)
        frappe.get_doc({"doctype": "DGI Gateway", "gateway_id": GW_D, "kind": "Poste navigateur",
                        "point_of_sale": PVF_D}).insert(ignore_permissions=True)
        frappe.db.commit()

    def propose(self, obs, user=CASHIER):
        from dgi_compliance.gateway.station import propose_device
        frappe.set_user(user)
        try:
            return propose_device(GW_D, dict(obs))
        finally:
            frappe.set_user("Administrator")

    def test_D01_proposal_creates_a_detected_mcf_never_active(self):
        before = count("MCF_PROPOSE")
        res = self.propose(self.OBS)
        self.assertEqual(res["nid"], "RD00000001")
        dev = frappe.get_doc("DGI MCF Device", "RD00000001")
        self.assertEqual((dev.status, dev.discovered_by, dev.discovered_gateway, dev.nif),
                         ("Detecte", CASHIER, GW_D, NIF))
        self.assertEqual(count("MCF_PROPOSE"), before + 1)
        self.assertFalse(R.equipped(frappe.get_doc("DGI Point of Sale", PVF_D), "MCF")[0])

    def test_D02_validation_rules(self):
        from dgi_compliance.fiscal.registry import validate_device
        # the cashier cannot validate
        frappe.set_user(CASHIER)
        try:
            with self.assertRaises(frappe.PermissionError):
                validate_device("RD00000001", PVF_D)
        finally:
            frappe.set_user("Administrator")
        # NIF of another taxpayer: refused
        self.propose(dict(self.OBS, mid="RD00000002-1", nif="B9999999Z"))
        with self.assertRaises(frappe.ValidationError):
            validate_device("RD00000002", PVF_D)
        self.assertNotEqual(frappe.db.get_value("DGI MCF Device", "RD00000002", "status"), "Actif")
        # two-person rule: proposed by Administrator, validated by Administrator
        self.propose(dict(self.OBS, mid="RD00000003-1"), user="Administrator")
        with self.assertRaises(frappe.ValidationError):
            validate_device("RD00000003", PVF_D)
        # another trade name: the difference must be accepted explicitly
        self.propose(dict(self.OBS, taxpayer_name="AUTRE NOM SARL"))
        with self.assertRaises(frappe.ValidationError):
            validate_device("RD00000001", PVF_D)
        res = validate_device("RD00000001", PVF_D, accept_differences="Ancien nom commercial, confirme par telephone")
        self.assertEqual(res["status"], "Actif")
        dev = frappe.get_doc("DGI MCF Device", "RD00000001")
        self.assertEqual((dev.validated_mid, dev.validated_by, dev.point_of_sale), ("RD00000001-1", "Administrator", PVF_D))
        self.assertEqual(frappe.db.get_value("DGI Point of Sale", PVF_D, "mcf_device"), "RD00000001")
        self.assertEqual(frappe.db.get_value("DGI Gateway", GW_D, "mcf_device"), "RD00000001")

    def test_D03_no_activation_by_hand_nor_without_reading(self):
        from dgi_compliance.fiscal.registry import validate_device
        if not frappe.db.exists("DGI MCF Device", "RD00000004"):
            frappe.get_doc({"doctype": "DGI MCF Device", "nid": "RD00000004", "status": "Declare"}).insert()
        dev = frappe.get_doc("DGI MCF Device", "RD00000004")
        dev.status, dev.point_of_sale = "Actif", PVF_D
        with self.assertRaises(frappe.ValidationError):
            dev.save()
        with self.assertRaises(frappe.ValidationError):           # never read: no NIF to check
            validate_device("RD00000004", PVF_D)

    def test_D04_mid_change_requires_a_new_validation(self):
        dev = frappe.get_doc("DGI MCF Device", "RD00000001")
        self.assertEqual(dev.status, "Actif")
        before = count("MID_CHANGE")
        self.assertEqual(ops.check_identity(dev, mid="RD00000001-2", nif=NIF), "MID_CHANGE")
        self.assertEqual(frappe.db.get_value("DGI MCF Device", "RD00000001", "status"), "A revalider")
        self.assertEqual(count("MID_CHANGE"), before + 1)
        self.assertFalse(R.equipped(frappe.get_doc("DGI Point of Sale", PVF_D), "MCF")[0])

    def test_D05_unknown_mcf_at_enrolment_is_recorded(self):
        from dgi_compliance.dgi_compliance.doctype.dgi_gateway.dgi_gateway import enroll_gateway, generate_enrollment_code
        from dgi_compliance.tests.test_hl2_mcf import TestBrowserStation
        frappe.db.set_value("DGI MCF Device", "RD00000001", "status", "Actif")
        _key, pub = TestBrowserStation._p256(None)
        code = generate_enrollment_code(GW_D)["code"]
        with self.assertRaises(frappe.ValidationError):
            enroll_gateway(GW_D, code, pub, {"mid": "RD00000009-1", "nif": NIF}, {}, enrolled_by=CASHIER)
        self.assertEqual(frappe.db.get_value("DGI MCF Device", "RD00000009", "status"), "Detecte")

    def test_D06_connectivity_test_is_recorded(self):
        from dgi_compliance.gateway.station import connectivity_test
        frappe.set_user(CASHIER)
        try:
            connectivity_test(GW_D, {"ok": True, "summary": "C1h OK, C2h en attente 0"})
        finally:
            frappe.set_user("Administrator")
        self.assertTrue(frappe.db.get_value("DGI MCF Device", "RD00000001", "last_connectivity_result").startswith("OK"))

    # ---- v5.3.1 : « Rechercher un MCF (sur ce PC) » depuis DGI Compliance Settings
    def test_D07_desk_search_proposes_a_detected_mcf_never_active(self):
        from dgi_compliance.fiscal.registry import propose_from_browser
        before = count("MCF_PROPOSE")
        res = propose_from_browser(json.dumps(dict(self.OBS, mid="RD00000007-1")), PVF_D)
        self.assertEqual((res["nid"], res["status"], res["created"]), ("RD00000007", "Detecte", True))
        dev = frappe.get_doc("DGI MCF Device", "RD00000007")
        self.assertEqual((dev.discovered_by, dev.discovered_gateway, dev.nif, dev.point_of_sale or None),
                         ("Administrator", None, NIF, None))
        obs = json.loads(dev.last_status)
        self.assertEqual((obs["proposed_for"], obs["read_by"]), (PVF_D, "browser-desk"))
        self.assertEqual(count("MCF_PROPOSE"), before + 1)

    def test_D08_desk_search_reserved_to_fiscal_managers(self):
        from dgi_compliance.fiscal.registry import propose_from_browser
        frappe.set_user(CASHIER)
        try:
            with self.assertRaises(frappe.PermissionError):
                propose_from_browser(json.dumps(dict(self.OBS, mid="RD00000008-1")), PVF_D)
        finally:
            frappe.set_user("Administrator")
        self.assertFalse(frappe.db.exists("DGI MCF Device", "RD00000008"))

    def test_D09_desk_search_refuses_an_mcf_active_elsewhere(self):
        from dgi_compliance.fiscal.registry import propose_from_browser
        frappe.db.set_value("DGI MCF Device", "RD00000001", {"status": "Actif", "point_of_sale": PVF_D})
        frappe.db.set_value("DGI Point of Sale", PVF_D, "mcf_device", "RD00000001")
        before = count("MCF_REFUSE")
        with self.assertRaises(frappe.ValidationError):
            propose_from_browser(json.dumps(self.OBS), PVF_K)
        self.assertEqual(count("MCF_REFUSE"), before + 1)
        self.assertEqual(frappe.db.get_value("DGI MCF Device", "RD00000001", "point_of_sale"), PVF_D)


# =========================================================================== section 10 : migration
class TestMigration(RoutageBase):
    def test_M01_patch_keeps_every_channel_and_is_idempotent(self):
        from dgi_compliance.patches.v5_3 import routage_dgi
        names = ("R MIGRATION A", "R MIGRATION B")
        for n in names:
            if not frappe.db.exists("DGI Point of Sale", n):
                d = frappe.new_doc("DGI Point of Sale")
                d.name = d.pos_name = n
                d.company, d.is_active = fx.COMPANY, 1
                d.db_insert()
        frappe.db.set_value("DGI Point of Sale", names[0], {"primary_channel": "e-MCF", "priority_reason": None,
                                                          "active_channel": "e-MCF", "forced_switch": None})
        frappe.db.set_value("DGI Point of Sale", names[1], {"primary_channel": "e-MCF", "priority_reason": None,
                                                          "active_channel": "MCF", "forced_switch": None})
        if not frappe.db.exists("DGI MCF Device", "RM00000001"):
            d = frappe.new_doc("DGI MCF Device")
            d.name = d.nid = "RM00000001"
            d.db_insert()
        frappe.db.set_value("DGI MCF Device", "RM00000001", {"status": "Actif", "last_mid": None, "validated_mid": None})
        for _i in range(2):
            routage_dgi.execute()
            a = frappe.db.get_value("DGI Point of Sale", names[0], ["primary_channel", "active_channel"], as_dict=True)
            b = frappe.db.get_value("DGI Point of Sale", names[1], ["primary_channel", "priority_reason", "active_channel"],
                                    as_dict=True)
            self.assertEqual((a.primary_channel, a.active_channel), (None, "e-MCF"))
            self.assertEqual((b.primary_channel, b.active_channel), ("MCF", "MCF"))
            self.assertIn("Reprise", b.priority_reason)
            self.assertEqual(frappe.db.get_value("DGI MCF Device", "RM00000001", "status"), "Declare")
        for n in names:
            frappe.db.set_value("DGI Point of Sale", n, "is_active", 0)
