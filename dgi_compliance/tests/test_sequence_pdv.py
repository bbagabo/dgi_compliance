"""Tests v5.4.0 - reference sequentielle par point de vente fiscal (Ref. PDV), fiscal.sequence.

Run on a TEST site:
    bench --site <site> run-tests --module dgi_compliance.tests.test_sequence_pdv

Scenarios Q01-Q18 (livrable v5.4.0, section 7): e-MCF, MCF, several points de vente with one ERPNext
naming series, e-MCF -> MCF fallback inside one point de vente, failures and retries that never consume a
number, idempotence, anti-duplicate guarantees (database keys, corrupted counter, real concurrent
transactions), protection of the field, year of the DEF Heure, print, hourly catch-up, series control.
The e-MCF and the MCF are simulators: no real fiscal invoice is emitted.
"""
import threading
import time
from unittest import mock

import frappe
from frappe.utils import now_datetime

from dgi_compliance.fiscal import fallback as FB
from dgi_compliance.fiscal import sequence as S
from dgi_compliance.tests import fixtures as fx
from dgi_compliance.tests.emcf_simulator import FakeResponse
from dgi_compliance.tests.test_hl2_mcf import NID_K, NID_L, PVF_K, PVF_L, PVF_N, HL2Base, _down, _upsert

PVF_Q = "Q SEQUENCE"
CODE_Q = "QSEQ01"
YEAR = now_datetime().year


def last(code, year=YEAR):
    return frappe.db.get_value(S.COUNTER, f"{code}-{year}", "last_number") or 0


def code_of(pvf):
    return frappe.db.get_value("DGI Point of Sale", pvf, "sequence_code")


def set_settings(**values):
    for k, v in values.items():
        frappe.db.set_single_value("DGI Compliance Settings", k, v)
    frappe.clear_document_cache("DGI Compliance Settings", "DGI Compliance Settings")
    frappe.clear_cache(doctype="DGI Compliance Settings")


class SequenceBase(HL2Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _upsert("DGI Point of Sale", PVF_Q, {"pos_name": PVF_Q, "company": fx.COMPANY, "is_active": 1,
                                             "is_sales_location": 0, "primary_channel": "e-MCF",
                                             "active_channel": "e-MCF", "token": "TOKEN-QSEQ",
                                             "fallback_policy": "Aucune", "sequence_code": CODE_Q,
                                             "nid_requirement": "Optional", "token_requirement": "Optional"})
        set_settings(sequence_enabled=1, sequence_scope=S.SCOPE_PVF, sequence_print_label=S.DEFAULT_LABEL)
        if not frappe.db.get_single_value("DGI Compliance Settings", "sequence_start_on"):
            set_settings(sequence_start_on=now_datetime())
        frappe.db.commit()

    def setUp(self):
        super().setUp()
        self.emcf.faults.clear()
        set_settings(sequence_enabled=1, sequence_scope=S.SCOPE_PVF)
        frappe.db.commit()

    def cycle(self, site):
        """The gateway API commits after each result: the reference is given at that commit."""
        out = site.cycle()
        frappe.db.commit()
        return out

    def emcf_invoice(self, pvf=PVF_Q, **extra):
        si = self.invoice(pvf=pvf, **extra)
        res = self.request(si)
        self.assertEqual(res.get("stage"), "normalized", res)
        return self.reload(si)

    def fake_normalized(self, pvf=PVF_Q, dt=None, channel="e-MCF"):
        """A draft carrying security elements written directly (no simulator): for unit scenarios."""
        si = self.invoice(pvf=pvf)
        frappe.db.set_value("Sales Invoice", si.name, {
            "custom_dgi_status": "Normalized", "custom_dgi_code_def": "TEST-" + frappe.generate_hash(length=20).upper(),
            "custom_dgi_nim": "XX01000001-1", "custom_dgi_counters": "1/1 FV", "custom_dgi_channel": channel,
            "custom_dgi_datetime": dt or now_datetime().strftime("%d/%m/%Y %H:%M:%S"),
            "custom_dgi_normalized_on": now_datetime()}, update_modified=False)
        return si.name


# =========================================================================== attribution
class TestAttribution(SequenceBase):
    def test_Q01_emcf_invoice_receives_the_next_reference_of_its_point_of_sale(self):
        before = last(CODE_Q)
        si = self.emcf_invoice()
        ref = f"{CODE_Q}-{YEAR}-{before + 1:06d}"
        self.assertEqual(si.dgi_sequence_ref, ref)
        self.assertNotEqual(si.name, ref)                                    # ERPNext number stays the main one
        e = frappe.get_doc(S.ENTRY, ref)
        self.assertEqual((e.sales_invoice, e.point_of_sale, e.channel, e.number, e.source),
                         (si.name, PVF_Q, "e-MCF", before + 1, "e-MCF"))
        self.assertEqual((e.dgi_device, e.dgi_counters, e.code_def),
                         (si.custom_dgi_nim, si.custom_dgi_counters, si.custom_dgi_code_def))
        si.submit()
        self.assertEqual(self.reload(si).dgi_sequence_ref, ref)              # kept by the submission

    def test_Q02_mcf_invoice_and_independent_series_per_point_of_sale(self):
        L = self.sites[PVF_L]
        bl, bq = last(code_of(PVF_L)), last(CODE_Q)
        a = self.invoice(pvf=PVF_L)
        self.request(a)
        self.cycle(L)
        a = self.reload(a)
        self.assertEqual(a.dgi_sequence_ref, f"{code_of(PVF_L)}-{YEAR}-{bl + 1:06d}")
        e = frappe.get_doc(S.ENTRY, a.dgi_sequence_ref)
        self.assertEqual((e.channel, e.source, e.operation), ("MCF", "MCF", a.custom_dgi_operation))
        self.assertTrue(e.dgi_device.startswith(NID_L))
        q = self.emcf_invoice()
        b = self.invoice(pvf=PVF_L)
        self.request(b)
        self.cycle(L)
        self.assertEqual(q.dgi_sequence_ref, f"{CODE_Q}-{YEAR}-{bq + 1:06d}")
        self.assertEqual(self.reload(b).dgi_sequence_ref, f"{code_of(PVF_L)}-{YEAR}-{bl + 2:06d}")
        # one ERPNext journal for both points de vente: names follow each other, references do not mix
        self.assertEqual(frappe.db.get_value("Sales Invoice", a.name, "naming_series"),
                         frappe.db.get_value("Sales Invoice", q.name, "naming_series"))

    def test_Q03_fallback_emcf_to_mcf_keeps_one_series_for_the_point_of_sale(self):
        K = self.kintambo
        code = code_of(PVF_K)
        before = last(code)
        first = self.emcf_invoice(pvf=PVF_K)                                # e-MCF of Kintambo
        si = self.invoice(pvf=PVF_K)
        with mock.patch("dgi_compliance.edef.client.requests.request", side_effect=_down), \
                mock.patch("dgi_compliance.edef.client.time.sleep"):
            res = self.request(si)
        self.assertEqual(res["stage"], "queued-mcf", res)
        self.assertFalse(self.reload(si).dgi_sequence_ref)                   # nothing given before the MCF answers
        self.cycle(K)
        si = self.reload(si)
        self.assertEqual(first.dgi_sequence_ref, f"{code}-{YEAR}-{before + 1:06d}")
        self.assertEqual(si.dgi_sequence_ref, f"{code}-{YEAR}-{before + 2:06d}")
        devices = {frappe.db.get_value(S.ENTRY, r, "dgi_device") for r in (first.dgi_sequence_ref, si.dgi_sequence_ref)}
        self.assertEqual(len(devices), 2)                                     # two DGI devices, one series
        self.assertTrue(any(d.startswith(NID_K) for d in devices))
        with mock.patch("frappe.enqueue", side_effect=lambda method, **kw: FB.renormalize(kw["invoice"])):
            FB.deactivate(PVF_K, "test")

    def test_Q04_refused_or_failed_transmissions_never_consume_a_number(self):
        before = last(CODE_Q)
        refused = lambda *a, **k: FakeResponse(200, {"errorCode": "9", "errorDesc": "groupe invalide"})  # noqa: E731
        si = self.invoice(pvf=PVF_Q)
        with mock.patch("dgi_compliance.edef.client.requests.request", side_effect=refused):
            self.assertFalse(self.request(si).get("ok"))
        with mock.patch("dgi_compliance.edef.client.requests.request", side_effect=_down), \
                mock.patch("dgi_compliance.edef.client.time.sleep"):
            self.assertFalse(self.request(si).get("ok"))                   # e-MCF down, no fallback (policy)
        self.assertEqual(last(CODE_Q), before)
        self.assertFalse(self.reload(si).dgi_sequence_ref)
        self.assertEqual(self.request(si)["stage"], "normalized")           # retry succeeds
        self.assertEqual(self.reload(si).dgi_sequence_ref, f"{CODE_Q}-{YEAR}-{before + 1:06d}")

    def test_Q05_unanswered_confirmation_gives_no_reference(self):
        before = last(CODE_Q)
        for _i in range(3):
            self.emcf.inject("PUT", "/api/invoice", "timeout")
        with mock.patch("dgi_compliance.edef.client.time.sleep"):
            si = self.invoice(pvf=PVF_Q)
            self.assertFalse(self.request(si).get("ok"))
        self.assertIn("INCERTAINE", (self.reload(si).custom_dgi_error or "").upper())
        self.assertFalse(self.reload(si).dgi_sequence_ref)
        self.assertEqual(last(CODE_Q), before)

    def test_Q06_idempotent_attribution_and_duplicate_mcf_result(self):
        si = self.emcf_invoice()
        ref = si.dgi_sequence_ref
        before = last(CODE_Q)
        self.assertEqual(S.assign(si.name, source="test"), ref)
        self.assertEqual(S.assign_safely(si.name, source="test"), ref)
        self.assertEqual(last(CODE_Q), before)
        L = self.sites[PVF_L]
        a = self.invoice(pvf=PVF_L)
        self.request(a)
        msg = L.lease()
        res = L.executor.execute(msg)
        L.send(res)
        frappe.db.commit()
        n = last(code_of(PVF_L))
        self.assertTrue(L.send(res).get("duplicate"))                       # the gateway sends it twice
        frappe.db.commit()
        self.assertEqual(last(code_of(PVF_L)), n)
        self.assertEqual(frappe.db.count(S.ENTRY, {"sales_invoice": a.name}), 1)

    def test_Q07_not_normalized_or_disabled_gives_nothing(self):
        si = self.invoice(pvf=PVF_Q)
        self.assertIsNone(S.assign(si.name, source="test"))
        set_settings(sequence_enabled=0)
        try:
            before = last(CODE_Q)
            si = self.emcf_invoice()
            self.assertFalse(si.dgi_sequence_ref)
            self.assertEqual(last(CODE_Q), before)
        finally:
            set_settings(sequence_enabled=1, sequence_start_on=now_datetime())    # as the settings form does
        self.assertEqual(S.assign_missing(), 0)          # invoices normalized while it was off stay without one
        self.assertFalse(self.reload(si).dgi_sequence_ref)


# =========================================================================== anti-duplicates
def _worker(site, sites_path, invoice, hold, barrier, out):
    frappe.init(site=site, sites_path=sites_path)
    frappe.connect()
    try:
        frappe.set_user("Administrator")
        barrier.wait(timeout=20)
        ref = S.assign(invoice, source="Test concurrence")
        time.sleep(hold)                                                     # keeps the counter locked
        frappe.db.commit()
        out[invoice] = ref
    except Exception as e:                                                   # noqa: BLE001
        frappe.db.rollback()
        out[invoice] = repr(e)
    finally:
        frappe.destroy()


class TestAntiDuplicates(SequenceBase):
    def test_Q08_database_keys_refuse_a_second_reference_or_a_second_line(self):
        si = self.emcf_invoice()
        src = frappe.get_doc(S.ENTRY, si.dgi_sequence_ref)
        e = {f: src.get(f) for f in ("reference", "sales_invoice", "sequence_code", "year", "number", "point_of_sale",
                                     "company", "channel", "dgi_device", "dgi_counters", "code_def")}
        dup = frappe.get_doc(dict(e, doctype=S.ENTRY, sales_invoice=self.fake_normalized()))
        dup.flags.dgi_sequence = True
        with self.assertRaises(frappe.DuplicateEntryError):                 # same reference
            dup.insert(ignore_permissions=True)
        frappe.db.rollback()
        other = frappe.get_doc(dict(e, doctype=S.ENTRY, reference=f"{CODE_Q}-1999-000001"))
        other.flags.dgi_sequence = True
        with self.assertRaises((frappe.DuplicateEntryError, frappe.UniqueValidationError)):  # same invoice
            other.insert(ignore_permissions=True)
        frappe.db.rollback()
        with self.assertRaises(frappe.ValidationError):                     # no line by hand
            frappe.get_doc(dict(e, doctype=S.ENTRY, reference="X", sales_invoice=None)).insert()
        with self.assertRaises(frappe.ValidationError):                     # append only
            frappe.delete_doc(S.ENTRY, si.dgi_sequence_ref, ignore_permissions=True)

    def test_Q09_corrupted_counter_never_reuses_a_number(self):
        si = self.emcf_invoice()
        top = frappe.db.get_value(S.ENTRY, si.dgi_sequence_ref, "number")
        frappe.db.sql(f"update `tab{S.COUNTER}` set last_number=%s where name=%s", (max(0, top - 3), f"{CODE_Q}-{YEAR}"))
        nxt = self.emcf_invoice()
        self.assertEqual(nxt.dgi_sequence_ref, f"{CODE_Q}-{YEAR}-{top + 1:06d}")
        self.assertTrue(frappe.db.exists("DGI Gateway Event", {"event_code": "SEQUENCE_INCOHERENTE"}))
        self.assertEqual(last(CODE_Q), top + 1)

    def test_Q10_two_concurrent_transactions_get_two_consecutive_numbers(self):
        a, b = self.fake_normalized(), self.fake_normalized()
        before = last(CODE_Q)
        frappe.db.commit()
        out, barrier = {}, threading.Barrier(2)
        site, sites_path = frappe.local.site, frappe.local.sites_path
        threads = [threading.Thread(target=_worker, args=(site, sites_path, inv, hold, barrier, out))
                   for inv, hold in ((a, 1.5), (b, 0.1))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        refs = sorted([out.get(a), out.get(b)])
        self.assertEqual(refs, [f"{CODE_Q}-{YEAR}-{before + 1:06d}", f"{CODE_Q}-{YEAR}-{before + 2:06d}"], out)
        self.assertEqual(last(CODE_Q), before + 2)

    def test_Q11_the_field_is_written_by_the_sfe_only(self):
        si = self.emcf_invoice()
        ref = si.dgi_sequence_ref
        si.dgi_sequence_ref = "FAUX-2026-000001"
        si.save()
        self.assertEqual(self.reload(si).dgi_sequence_ref, ref)              # draft: stored value kept
        si = self.reload(si)
        si.submit()
        si = self.reload(si)
        si.dgi_sequence_ref = None
        si.save()                                                            # update after submit
        self.assertEqual(self.reload(si).dgi_sequence_ref, ref)
        copy = frappe.copy_doc(si, ignore_no_copy=False)
        self.assertFalse(copy.get("dgi_sequence_ref"))                      # no_copy: duplicate / amend
        new = self.invoice(pvf=PVF_Q, dgi_sequence_ref="FAUX-2026-000002")
        self.assertFalse(self.reload(new).dgi_sequence_ref)


# =========================================================================== rules, print, control
class TestRulesAndPrint(SequenceBase):
    def test_Q12_year_of_the_def_heure_and_yearly_restart(self):
        self.assertEqual(S.year_of("31/12/2025 23:59:59"), 2025)
        self.assertEqual(S.year_of("01/01/2027 00:00:05"), 2027)
        self.assertEqual(S.year_of(None), now_datetime().year)
        nxt = YEAR + 7                                                       # a year never used by the tests
        frappe.db.sql(f"delete from `tab{S.ENTRY}` where sequence_code=%s and year=%s", (CODE_Q, nxt))
        frappe.db.sql(f"delete from `tab{S.COUNTER}` where name=%s", f"{CODE_Q}-{nxt}")
        inv = self.fake_normalized(dt=f"01/01/{nxt} 00:00:05")
        self.assertEqual(S.assign(inv, source="test"), f"{CODE_Q}-{nxt}-000001")

    def test_Q13_codes_are_validated_unique_and_locked_once_used(self):
        self.assertEqual(S.clean_code(" kin-01 é"), "KIN01E")
        pvf = frappe.get_doc("DGI Point of Sale", PVF_N)
        pvf.sequence_code = CODE_Q
        with self.assertRaises(frappe.ValidationError):                     # unique
            pvf.save()
        pvf.reload()
        pvf.sequence_code = "X"
        with self.assertRaises(frappe.ValidationError):                     # format
            pvf.save()
        self.emcf_invoice()
        q = frappe.get_doc("DGI Point of Sale", PVF_Q)
        q.sequence_code = "QSEQ99"
        with self.assertRaises(frappe.ValidationError):                     # already used for references
            q.save()
        s = frappe.get_single("DGI Compliance Settings")
        s.sequence_default_code = CODE_Q
        s.flags.ignore_mandatory = True
        with self.assertRaises(frappe.ValidationError):                     # default code = a PVF code
            s.save(ignore_permissions=True)
        h = frappe.generate_hash(length=6).upper()
        new = frappe.get_doc({"doctype": "DGI Point of Sale", "pos_name": "Q AUTO " + h, "company": fx.COMPANY,
                              "is_active": 0, "pos_code": f"k-{h.lower()}", "nid_requirement": "Optional",
                              "token_requirement": "Optional"}).insert()
        self.assertEqual(new.sequence_code, f"K{h}")                         # proposed from the Code magasin
        named = frappe.get_doc({"doctype": "DGI Point of Sale", "pos_name": "Kinshasa Gombe " + h,
                                "company": fx.COMPANY, "is_active": 0, "nid_requirement": "Optional",
                                "token_requirement": "Optional"}).insert()
        self.assertTrue(named.sequence_code.startswith("KINSH"), named.sequence_code)   # else from the name

    def test_Q14_print_shows_the_reference_under_the_number_in_smaller_type(self):
        si = self.emcf_invoice()
        si.submit()
        from dgi_compliance.edef.invoice_print import dgi_invoice_context
        ctx = dgi_invoice_context(self.reload(si))
        self.assertEqual((ctx["general"]["sequence_ref"], ctx["general"]["sequence_label"]),
                         (si.dgi_sequence_ref, S.DEFAULT_LABEL))
        html = frappe.get_print("Sales Invoice", si.name, "DGI Facture Normalisee")
        line = f"{S.DEFAULT_LABEL} : {si.dgi_sequence_ref}"
        self.assertEqual(html.count(line), 2)                                # title block and general information
        self.assertLess(html.index(f"N° {si.name}"), html.index(line))
        self.assertIn(f'<div class="title-sub ref2">{line}</div>', html)
        self.assertIn(".fn .ref2{font-size:8.5px", html)
        legacy = frappe.get_print("Sales Invoice", si.name, "DGI Sales Invoice")
        self.assertIn(si.dgi_sequence_ref, legacy)
        set_settings(sequence_print_label="Réf. séquence")
        try:
            self.assertIn(f"Réf. séquence : {si.dgi_sequence_ref}", frappe.get_print("Sales Invoice", si.name,
                                                                                 "DGI Facture Normalisee"))
        finally:
            set_settings(sequence_print_label=S.DEFAULT_LABEL)

    def test_Q15_failed_attribution_keeps_the_fiscal_fact_and_is_caught_up(self):
        with mock.patch("dgi_compliance.fiscal.sequence.assign", side_effect=Exception("verrou")):
            si = self.emcf_invoice()
        self.assertEqual(si.custom_dgi_status, "Normalized")                # security elements kept
        self.assertFalse(si.dgi_sequence_ref)
        self.assertTrue(frappe.db.exists("DGI Gateway Event", {"event_code": "REFERENCE_EN_ATTENTE"}))
        self.assertGreaterEqual(S.assign_missing(), 1)
        ref = self.reload(si).dgi_sequence_ref
        self.assertTrue(ref and ref.startswith(f"{CODE_Q}-{YEAR}-"), ref)
        self.assertEqual(frappe.db.get_value(S.ENTRY, ref, ["sales_invoice", "source"]), (si.name, "Rattrapage"))
        self.assertTrue(S.check_sequence(CODE_Q, YEAR)["ok"])

    def test_Q16_invoices_normalized_before_activation_are_not_numbered(self):
        inv = self.fake_normalized()
        frappe.db.set_value("Sales Invoice", inv, "custom_dgi_normalized_on", "2020-01-01 00:00:00")
        S.assign_missing()
        self.assertFalse(frappe.db.get_value("Sales Invoice", inv, "dgi_sequence_ref"))
        with self.assertRaises(frappe.ValidationError):
            S.assign_now(inv)

    def test_Q17_company_scope_and_invoices_without_point_of_sale(self):
        set_settings(sequence_scope=S.SCOPE_COMPANY)
        try:
            code = S.clean_code(frappe.db.get_value("Company", fx.COMPANY, "abbr"))
            before = last(code)
            si = self.emcf_invoice()
            self.assertEqual(si.dgi_sequence_ref, f"{code}-{YEAR}-{before + 1:06d}")
        finally:
            set_settings(sequence_scope=S.SCOPE_PVF)
        code, scope = S.scope_of(None, fx.COMPANY)
        self.assertEqual((code, scope), (S.clean_code(frappe.db.get_single_value("DGI Compliance Settings",
                                                                                 "sequence_default_code")), S.SCOPE_GLOBAL))

    def test_Q18_series_control_detects_a_gap(self):
        self.emcf_invoice()
        self.emcf_invoice()
        res = S.check_sequence(CODE_Q, YEAR)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["counter"], last(CODE_Q))
        victim = frappe.db.sql(f"select name, number from `tab{S.ENTRY}` where sequence_code=%s and year=%s "
                               "order by number desc limit 1 offset 1", (CODE_Q, YEAR), as_dict=True)[0]
        frappe.db.sql(f"update `tab{S.ENTRY}` set number=-number where name=%s", victim.name)   # simulated tampering
        try:
            res = S.check_sequence(CODE_Q, YEAR)
            self.assertFalse(res["ok"])
            self.assertIn(victim.number, res["gaps"])
        finally:
            frappe.db.sql(f"update `tab{S.ENTRY}` set number=-number where name=%s", victim.name)
        self.assertTrue(S.check_sequence(CODE_Q, YEAR)["ok"])
