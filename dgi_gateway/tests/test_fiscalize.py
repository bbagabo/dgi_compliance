from dgi_gateway import protocol as P
from dgi_gateway.device import LinkError, MCFDevice
from dgi_gateway.fiscalize import Executor
from dgi_gateway.simulator import MCFSimulator

from conftest import NID, NIF, make_op


def ex(device, journal, **kw):
    return Executor(device, journal, pending_interval=0.01, **kw)


def test_normalize_happy_path(sim, device, journal):
    res = ex(device, journal).execute(make_op())
    assert res["outcome"] == "normalized", res
    d = res["data"]
    assert d["fn"] == "SINV-0001" and P.SIG_RE.match(d["sig"])
    assert P.nid_of(d["mid"]) == NID and d["nif"] == NIF
    assert d["qr"] == f"RDCDEF01;{d['mid']};{d['sig']};{NIF};{d['dt']}"
    assert len(sim.invoices) == 1 and sim.invoices[0]["mv"] == 2820
    assert journal.get("OP-1")["state"] == "DONE"


def test_spec_layout_and_fn_sig_order(journal):
    sim = __import__("dgi_gateway.simulator", fromlist=["MCFSimulator"]).MCFSimulator(
        nid=NID, nif=NIF, subtotal_layout="spec35", fn_sig_order="fn_sig")
    res = ex(MCFDevice(sim), journal).execute(make_op())
    assert res["outcome"] == "normalized"
    assert res["data"]["fn"] == "SINV-0001"


def test_ht_mode(sim, device, journal):
    items = [{"name": "Service", "itype": "SER", "tax_group": "B", "tax_rate": 16, "amount": 1000,
              "price": 1000, "qty": 1}]
    res = ex(device, journal).execute(make_op(items=items, pmode="HT", totals={"mv": 1160, "mt": 160},
                                              payments=[{"letter": "V", "amount": 1160}]))
    assert res["outcome"] == "normalized", res
    assert sim.invoices[0]["mt"] == 160


def test_wrong_device_is_refused_before_any_invoice_command(sim, device, journal):
    res = ex(device, journal).execute(make_op(nid="OTHER00001"))
    assert res["outcome"] == "rejected" and res["error"]["code"] == "NID_INATTENDU"
    assert P.CMD_OPEN not in sim.executed and not sim.invoices


def test_wrong_nif_is_refused(sim, device, journal):
    res = ex(device, journal).execute(make_op(nif="B9999999Z"))
    assert res["error"]["code"] == "NIF_INATTENDU" and not sim.invoices


def test_totals_mismatch_cancels_invoice(sim, device, journal):
    res = ex(device, journal).execute(make_op(totals={"mv": 2900, "mt": 320}))
    assert res["outcome"] == "rejected" and res["error"]["code"] == "ECART_TOTAUX"
    assert res["proven_not_normalized"] and not sim.invoices
    assert sim._inv is None                      # invoice cancelled on the MCF


def test_rate_mismatch_refused(sim, device, journal):
    items = [{"name": "X", "itype": "BIE", "tax_group": "C", "tax_rate": 8, "amount": 100, "price": 100, "qty": 1}]
    res = ex(device, journal).execute(make_op(items=items, totals={"mv": 100, "mt": 7.41}))
    assert res["error"]["code"] == "TAUX_DIFFERENTS" and P.CMD_OPEN not in sim.executed


def test_leftover_open_invoice_is_cancelled_first(sim, device, journal):
    device.set_client({"ctype": "PP"})
    device.open_invoice(make_op()["invoice"] | {"fn": "ORPHELINE"})
    assert sim._inv is not None
    res = ex(device, journal).execute(make_op(fn="SINV-0002"))
    assert res["outcome"] == "normalized"
    assert [i["fn"] for i in sim.invoices] == ["SINV-0002"]


def test_lost_38h_answer_is_retransmitted_without_double_normalization(sim, device, journal):
    sim.lose_response_for = {P.CMD_FINALIZE}       # executed, answer lost once
    res = ex(device, journal).execute(make_op())
    assert res["outcome"] == "normalized"
    assert len(sim.invoices) == 1                  # same SEQ resent: MCF answered from its cache


def test_unplugged_during_38h_then_recovered_by_c1h(sim, journal):
    class Unplug(MCFDevice):
        def finalize(self, *a, **k):
            r = super().finalize(*a, **k)           # the MCF normalizes ...
            raise LinkError("cable arrache")        # ... but the gateway never gets the answer
    dev = Unplug(sim)
    res = ex(dev, journal).execute(make_op())
    assert res["outcome"] == "normalized" and res["data"]["recovered"] is True
    assert res["data"]["sig"] == sim.invoices[0]["sig"] and len(sim.invoices) == 1


def test_unplugged_during_38h_and_still_unreadable_is_uncertain(sim, journal):
    class Unplug(MCFDevice):
        def finalize(self, *a, **k):
            super().finalize(*a, **k)
            sim.unplugged = True
            raise LinkError("cable arrache")
    res = ex(Unplug(sim), journal).execute(make_op())
    assert res["outcome"] == "uncertain"
    assert journal.get("OP-1")["state"] == "UNCERTAIN"
    sim.unplugged = False                           # cable back: reconciliation settles it
    res2 = ex(MCFDevice(sim), journal).check(make_op())
    assert res2["outcome"] == "normalized" and res2["data"]["recovered"]
    assert len(sim.invoices) == 1


def test_check_proves_non_normalization(sim, device, journal):
    op = make_op()
    journal.record_received(op)
    journal.set_state("OP-1", "FINALIZING")
    res = ex(device, journal).check(op)
    assert res["outcome"] == "rejected" and res["proven_not_normalized"]
    assert res["error"]["code"] == "NON_NORMALISEE"


def test_same_lease_twice_returns_stored_result(sim, device, journal):
    e = ex(device, journal)
    r1 = e.execute(make_op())
    r2 = e.execute(make_op())
    assert r1["data"]["sig"] == r2["data"]["sig"] and len(sim.invoices) == 1


def test_new_operation_for_already_normalized_invoice_replays(sim, device, journal):
    e = ex(device, journal)
    e.execute(make_op("OP-1"))
    r = e.execute(make_op("OP-2"))
    assert r["outcome"] == "normalized" and r["data"]["replay_of"] == "OP-1" and len(sim.invoices) == 1


def test_journal_lost_but_mcf_last_invoice_matches(sim, device, journal, tmp_path):
    ex(device, journal).execute(make_op("OP-1"))
    from dgi_gateway.journal import Journal
    fresh = Journal(str(tmp_path / "new.sqlite3"))
    r = ex(device, fresh).execute(make_op("OP-1"))
    assert r["outcome"] == "normalized" and r["data"]["recovered"] and len(sim.invoices) == 1


def test_pending_then_success(sim, device, journal):
    sim.pending_38 = 2
    res = ex(device, journal).execute(make_op())
    assert res["outcome"] == "normalized"


def test_credit_note_offline_is_retryable_rejection(sim, device, journal):
    ex(device, journal).execute(make_op())
    ref = P.format_sig(sim.invoices[0]["sig"])
    sim.online = False
    op = make_op("OP-2", "SINV-0002", itype="FA", credit_note={"rr": "RAN", "rn": ref})
    res = ex(device, journal).execute(op)
    assert res["outcome"] == "rejected" and res["error"]["code"] == "AVOIR_NON_VERIFIABLE"
    assert res["error"]["retryable"] and len(sim.invoices) == 1 and sim._inv is None


def test_credit_note_online(sim, device, journal):
    ex(device, journal).execute(make_op())
    op = make_op("OP-2", "SINV-0002", itype="FA",
                 credit_note={"rr": "RAN", "rn": P.format_sig(sim.invoices[0]["sig"])})
    res = ex(device, journal).execute(op)
    assert res["outcome"] == "normalized" and res["data"]["ft"] == "FA"


def test_exchange_rate_refusal_is_not_retried_silently(sim, device, journal):
    sim.reject_fx = True
    res = ex(device, journal).execute(make_op(exchange={"crt": 2830.75, "cdt": "20261031"}))
    assert res["outcome"] == "rejected" and res["error"]["code"] == "TAUX_CHANGE_REFUSE"
    assert not sim.invoices


def test_nak_and_syn(sim, device, journal):
    sim.nak_next = 2
    sim.syn_before = 3
    res = ex(device, journal).execute(make_op())
    assert res["outcome"] == "normalized"


def test_resync(sim, device, journal):
    res = ex(device, journal).execute({"operation_id": "R1", "op_type": "RESYNC"})
    assert res["outcome"] == "done" and res["data"]["server_status"]["state"] == "RES"


def test_updated_mcf_with_group_q_and_credit_letter(journal):
    """H5 + H6: an updated MCF (19 groups, credit letter R) normalizes a Q-group credit sale."""
    sim = MCFSimulator(nid=NID, nif=NIF, groups=19, rates={"B": 16.0, "Q": 8.0}, extra_payment_letters=("R",))
    dev = MCFDevice(sim, response_timeout=0.5)
    op = make_op(items=[{"name": "Article Q", "itype": "BIE", "tax_group": "Q", "tax_rate": 8,
                         "amount": 1080, "price": 1080, "qty": 1}],
                 payments=[{"letter": "R", "amount": 1080}], totals={"mv": 1080, "mt": 80})
    res = Executor(dev, journal).execute(op)
    assert res["outcome"] == "normalized", res
    assert sim.invoices[-1]["fn"] == "SINV-0001"


def test_protocol_v1_mcf_refuses_group_q(journal):
    sim = MCFSimulator(nid=NID, nif=NIF)          # 16 groups: not updated
    dev = MCFDevice(sim, response_timeout=0.5)
    op = make_op(items=[{"name": "Article Q", "itype": "BIE", "tax_group": "Q", "tax_rate": 8,
                         "amount": 1080, "price": 1080, "qty": 1}], totals={"mv": 1080, "mt": 80})
    res = Executor(dev, journal).execute(op)
    assert res["outcome"] == "rejected" and res.get("proven_not_normalized")
    assert res["error"]["code"] == "GROUPE_INCONNU_MCF" and not res["error"]["retryable"]
    assert not sim.invoices and 0xC0 not in sim.executed       # refused before opening an invoice


def test_unknown_credit_letter_is_a_clear_refusal(journal):
    sim = MCFSimulator(nid=NID, nif=NIF)          # no credit letter
    dev = MCFDevice(sim, response_timeout=0.5)
    res = Executor(dev, journal).execute(make_op(payments=[{"letter": "R", "amount": 2820}]))
    assert res["outcome"] == "rejected" and res["error"]["code"] == "PAIEMENT_INCONNU_MCF"
    assert not sim.invoices and not P.invoice_open(sim._sta())
