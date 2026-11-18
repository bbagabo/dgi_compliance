"""Protocol MCF-SFE v2 (DGI, October 2026): C1h and 33h with ST1 / ST2, groups A-Z, T-Z reserved.

Annexe Protocoles MCF-SFE v2.0 and Guide de migration v2:
  C1h  (no data | ST1) -> MID..DMV, TAXA..TAXP     ST2 -> TAXQ..TAXZ
  33h  (no data | ST1) -> MV, MVA..MVP, MTA..MTP, MTTS, MCUR     ST2 -> MVQ..MVZ, MTQ..MTZ
"""
import pytest

from dgi_gateway import protocol as P
from dgi_gateway.device import MCFDevice
from dgi_gateway.fiscalize import Executor
from dgi_gateway.simulator import MCFSimulator

from conftest import NID, NIF, make_op

V2_RATES = {"B": 16.0, "C": 5.0, "F": 16.0, "G": 5.0, "O": 1.0, "P": 1.0, "Q": 8.0, "R": 8.0}

# Q 8 %: 1 080 TTC -> VAT 80 ; B 16 %: 2 320 TTC -> VAT 320 ; S (hors champ): 500
ITEMS_V2 = [
    {"name": "Lait en poudre", "itype": "BIE", "tax_group": "B", "tax_rate": 16, "amount": 2320, "price": 1160, "qty": 2},
    {"name": "Farine (8 %)", "itype": "BIE", "tax_group": "Q", "tax_rate": 8, "amount": 1080, "price": 1080, "qty": 1},
    {"name": "Indemnite", "itype": "SER", "tax_group": "S", "tax_rate": 0, "amount": 500, "price": 500, "qty": 1},
]
TOTALS_V2 = {"mv": 3900, "mt": 400}


def ex(device, journal, **kw):
    return Executor(device, journal, pending_interval=0.01, **kw)


def v2_sim(**kw):
    kw.setdefault("rates", V2_RATES)
    return MCFSimulator(nid=NID, nif=NIF, protocol=2, **kw)


def op_v2(**kw):
    kw.setdefault("items", ITEMS_V2)
    kw.setdefault("totals", TOTALS_V2)
    kw.setdefault("payments", [{"letter": "E", "amount": TOTALS_V2["mv"]}])
    return make_op(**kw)


# ---------------------------------------------------------------- parsing
def test_constants_follow_the_specification():
    assert P.HISTORIC_LETTERS == "ABCDEFGHIJKLMNOP" and P.ST2_LETTERS == "QRSTUVWXYZ"
    assert P.APPLICABLE_LETTERS == "ABCDEFGHIJKLMNOPQRS" and P.RESERVED_LETTERS == "TUVWXYZ"


def test_c1h_st2_example_of_the_specification():
    assert P.parse_status_st2("0,0,0,0,0,0,0,0,0,0") == {x: 0.0 for x in "QRSTUVWXYZ"}
    rates = P.parse_status_st2("8.00,8.00,0.00,0,0,0,0,0,0,0")
    assert rates["Q"] == 8.0 and rates["R"] == 8.0 and rates["S"] == 0.0 and len(rates) == 10


@pytest.mark.parametrize("answer", [
    "AB01000001-1,A1234567B,20260801100320,21,11,10,20260801100305,FV,QWERTY,FAC/1,1500.00," + ",".join(["0"] * 16),
    "E:COMMANDE NON VALIDE",
    "",
    ",".join(["0"] * 11),
])
def test_c1h_st2_answer_of_a_v1_mcf_is_refused(answer):
    with pytest.raises(P.ProtocolError):
        P.parse_status_st2(answer)


def test_merge_status_keeps_a_to_p_and_adds_q_to_z():
    st = P.parse_status("AB01000001-1,A1234567B,20260801100320,21000000,11000000,10000000,20260801100305,FV,"
                        "QWERTYUIOPLKJHGFDSAZXCVB,FAC/KI/2026/1223345,1500.00,0,16,5,0,0,16,5,0,0,0,0,0,0,0,1,1")
    assert st["group_count"] == 16 and st["tax_rates"]["B"] == 16.0
    merged = P.merge_status_st2(st, P.parse_status_st2("8,8,0,0,0,0,0,0,0,0"))
    assert merged["protocol"] == 2 and merged["group_count"] == 26
    assert merged["tax_rates"]["P"] == 1.0 and merged["tax_rates"]["Q"] == 8.0 and merged["tax_rates"]["Z"] == 0.0


def test_33h_st2_example_of_the_specification():
    r = P.parse_subtotal_st2(",".join(["0"] * 20))
    assert r["group_count"] == 10 and r["groups"] == {}
    f = ["1080.00", "0", "500.00"] + ["0"] * 7 + ["80.00"] + ["0"] * 9
    r = P.parse_subtotal_st2(",".join(f))
    assert r["groups"] == {"Q": {"total": 1080.0, "vat": 80.0}, "S": {"total": 500.0, "vat": 0.0}}


@pytest.mark.parametrize("answer", ["E:COMMANDE NON VALIDE", ",".join(["0"] * 21), ",".join(["0"] * 22), "A,B"])
def test_33h_st2_unreadable(answer):
    with pytest.raises(P.ProtocolError):
        P.parse_subtotal_st2(answer)


def test_merge_subtotal_vat_total_includes_q_to_z_spec_layout():
    st1 = ["3900.00"] + ["0", "2320.00"] + ["0"] * 14 + ["0", "320.00"] + ["0"] * 14 + ["0", "0"]
    sub = P.parse_subtotal(",".join(st1), 16)
    assert sub["layout"] == "spec35" and sub["mt"] == 320.0          # A-P only
    st2 = ["1080.00", "0", "500.00"] + ["0"] * 7 + ["80.00"] + ["0"] * 9
    merged = P.merge_subtotal(sub, P.parse_subtotal_st2(",".join(st2)))
    assert merged["mv"] == 3900.0 and merged["mt"] == 400.0           # VAT of Q added
    assert set(merged["groups"]) == {"B", "Q", "S"} and merged["group_count"] == 26


def test_merge_subtotal_eltrade_keeps_its_vat_field():
    st1 = ["3900.00", "400.00"] + ["0", "2320.00"] + ["0"] * 14 + ["0", "320.00"] + ["0"] * 14 + ["0", "0"]
    sub = P.parse_subtotal(",".join(st1), 16)
    merged = P.merge_subtotal(sub, P.parse_subtotal_st2(",".join(["1080.00"] + ["0"] * 9 + ["80.00"] + ["0"] * 9)))
    assert merged["mt"] == 400.0 and merged["groups"]["Q"]["vat"] == 80.0


def test_item_of_any_letter_a_to_z_is_encoded():
    assert ",S0.00%500.00\t" in P.item_data({"name": "X", "tax_group": "S", "tax_rate": 0, "amount": 500, "price": 500})
    assert ",Z0.00%1.00\t" in P.item_data({"name": "X", "tax_group": "Z", "tax_rate": 0, "amount": 1, "price": 1})


# ---------------------------------------------------------------- device
def test_device_reads_q_to_z_with_st2(journal):
    sim = v2_sim(subtotal_layout="spec35")
    dev = MCFDevice(sim, response_timeout=0.5)
    st = dev.status()
    assert st["protocol"] == 2 and dev.protocol == 2 and st["group_count"] == 26
    assert st["tax_rates"]["Q"] == 8.0 and st["tax_rates"]["R"] == 8.0 and st["tax_rates"]["B"] == 16.0
    assert sim.executed == [P.CMD_STATUS, P.CMD_STATUS]


def test_device_v1_mcf_stays_on_a_to_p_and_st2_is_asked_once(sim, device):
    st = device.status()
    assert st["protocol"] == 1 and st["group_count"] == 16 and "Q" not in st["tax_rates"] and st.get("st2_error")
    device.status()
    assert sim.executed.count(P.CMD_STATUS) == 3          # C1h, C1h ST2, then C1h only


def test_device_v1_mcf_refusing_st2():
    sim = MCFSimulator(nid=NID, nif=NIF, st2_refused=True)
    st = MCFDevice(sim, response_timeout=0.5).status()
    assert st["protocol"] == 1 and "refusee" in st["st2_error"]


def test_interim_firmware_with_19_groups_inline_needs_no_st2():
    sim = MCFSimulator(nid=NID, nif=NIF, groups=19, rates=V2_RATES)
    dev = MCFDevice(sim, response_timeout=0.5)
    st = dev.status()
    assert st["group_count"] == 19 and st["tax_rates"]["Q"] == 8.0
    assert sim.executed == [P.CMD_STATUS]                 # ST2 not asked: the groups are already known


# ---------------------------------------------------------------- normalization
@pytest.mark.parametrize("layout", ["spec35", "eltrade36"])
def test_invoice_with_groups_b_q_s_on_a_v2_mcf(journal, layout):
    sim = v2_sim(subtotal_layout=layout)
    res = ex(MCFDevice(sim, response_timeout=0.5), journal).execute(op_v2())
    assert res["outcome"] == "normalized", res
    assert sim.invoices[0]["mv"] == 3900 and sim.invoices[0]["mt"] == 400
    assert res["data"]["machine_mt"] == 400.0
    assert sim.executed.count(P.CMD_SUBTOTAL) == 2       # 33h then 33h ST2


def test_invoice_a_to_p_on_a_v2_mcf_still_reads_st2(journal):
    sim = v2_sim()
    res = ex(MCFDevice(sim, response_timeout=0.5), journal).execute(make_op())
    assert res["outcome"] == "normalized", res
    assert sim.invoices[0]["mt"] == 320


def test_group_q_on_a_v1_mcf_is_refused_before_any_invoice_command(sim, device, journal):
    res = ex(device, journal).execute(op_v2())
    assert res["outcome"] == "rejected" and res["error"]["code"] == "GROUPE_INCONNU_MCF"
    assert "protocole MCF-SFE v1" in res["error"]["message"] and "ST2" in res["error"]["message"]
    assert P.CMD_OPEN not in sim.executed and not sim.invoices and res["proven_not_normalized"]


def test_reserved_group_is_refused_even_by_a_v2_mcf(journal):
    sim = v2_sim()
    items = [{"name": "X", "itype": "BIE", "tax_group": "T", "tax_rate": 0, "amount": 100, "price": 100, "qty": 1}]
    res = ex(MCFDevice(sim, response_timeout=0.5), journal).execute(
        make_op(items=items, totals={"mv": 100, "mt": 0}, payments=[{"letter": "E", "amount": 100}]))
    assert res["error"]["code"] == "GROUPE_RESERVE" and P.CMD_OPEN not in sim.executed


def test_ht_mode_with_group_r_on_a_v2_mcf(journal):
    sim = v2_sim()
    items = [{"name": "Marche public", "itype": "SER", "tax_group": "R", "tax_rate": 8, "amount": 1000,
              "price": 1000, "qty": 1}]
    res = ex(MCFDevice(sim, response_timeout=0.5), journal).execute(
        make_op(items=items, pmode="HT", totals={"mv": 1080, "mt": 80}, payments=[{"letter": "V", "amount": 1080}]))
    assert res["outcome"] == "normalized", res
    assert sim.invoices[0]["mt"] == 80
