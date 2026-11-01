import pytest

from dgi_gateway import protocol as P


def test_bcc_example_from_spec():
    # "12ABh est transfere comme 31h 32h 3Ah 3Bh"
    core = bytes([0xFF] * 18 + [0xBD])          # sum = 0x12AB
    assert P.bcc16(core) == bytes([0x31, 0x32, 0x3A, 0x3B])


def test_frame_roundtrip_command():
    frame = P.build_frame(0x21, P.CMD_STATUS, b"")
    assert frame[0] == P.SOH and frame[-1] == P.ETX
    seq, cmd, data = P.parse_command_frame(frame)
    assert (seq, cmd, data) == (0x21, P.CMD_STATUS, b"")


def test_data_too_long_refused():
    with pytest.raises(P.ProtocolError):
        P.build_frame(0x21, P.CMD_ITEM, b"x" * 201)


def test_seq_wraps_in_range():
    assert P.next_seq(0xFF) == 0x20
    assert P.next_seq(0x20) == 0x21


def test_escape_and_truncate():
    s = P.escape_text("A,B<C>&D\r\n\tE")
    assert s == "A^x2c;B^lt;C^gt;^amp;D^xa;^xd; E"
    assert P.unescape_text(s) == "A,B<C>&D\r\n E"
    cut = P.truncate_utf8(P.escape_text("AB,CD"), 4)
    assert cut == "AB"            # never cuts inside ^x2c;


def test_item_examples():
    # Example 3: 2 x JUS 930 TTC
    d = P.item_data({"name": "JUS", "itype": "BIE", "tax_group": "B", "tax_rate": 16, "amount": 1860,
                     "price": 930, "qty": 2})
    assert d == "JUS\tBIE,B16.00%1860.00\t930.00*2"
    # Example 6 with original price
    d = P.item_data({"name": "SPEC", "itype": "BIE", "tax_group": "B", "tax_rate": 16, "amount": 2000,
                     "price": 500, "qty": 4, "orig_price": 1000, "price_desc": "reduction 50%"})
    assert d.endswith("500.00*4\t1000.00,reduction 50%")
    # specific tax
    d = P.item_data({"name": "BIERE", "itype": "BIE", "tax_group": "B", "tax_rate": 16, "amount": 2000,
                     "price": 500, "qty": 4, "ts_value": "75", "ts_total": 300})
    assert d.endswith("500.00*4;T75,300.00")


def test_item_groups_a_to_z_accepted_others_refused():
    # H6: FACNO v2.0 groups (Q, R, S...) are sent to an updated MCF; the device decides
    assert ",Q8.00%1.00\t" in P.item_data({"name": "X", "tax_group": "Q", "tax_rate": 8, "amount": 1, "price": 1})
    for bad in ("", "1", "AB", "?"):
        with pytest.raises(P.ProtocolError):
            P.item_data({"name": "X", "tax_group": bad, "tax_rate": 8, "amount": 1, "price": 1})


def test_payment_letter_any_single_letter():
    # H5: the credit letter of the updated MCF is configured in ERPNext
    assert P.payment_data("R", 10) == "R10.00"
    for bad in ("", "RR", "1", "é"):
        if bad == "":
            assert P.payment_data(bad) == ""
            continue
        with pytest.raises(P.ProtocolError):
            P.payment_data(bad, 1)


def test_status_reports_more_groups_after_update():
    rates = ",".join(["0.00", "16.00"] + ["0.00"] * 14 + ["8.00", "0.00", "0.00"])     # A..S
    st = P.parse_status("AB01000001-1,A1,20260301100320,49,46,3,20260301100305,FV,"
                        "QWERTYUIOPLKJHGFDSAZXCVB,FAC05,13000.00," + rates)
    assert st["group_count"] == 19 and st["tax_rates"]["Q"] == 8.0 and st["tax_rates"]["B"] == 16.0


@pytest.mark.parametrize("groups,layout", [(16, "spec"), (16, "eltrade"), (19, "spec"), (19, "eltrade"),
                                           (26, "spec"), (26, "eltrade")])
def test_subtotal_any_number_of_groups(groups, layout):
    mva = ["0.00"] * groups
    mta = ["0.00"] * groups
    mva[1], mta[1] = "116.00", "16.00"          # B
    if groups > 16:
        mva[16], mta[16] = "108.00", "8.00"     # Q
    head = ["224.00"] + (["24.00"] if layout == "eltrade" else [])
    text = ",".join(head + mva + mta + ["0.00", "0"])
    r = P.parse_subtotal(text, groups)
    assert r["group_count"] == groups and r["mv"] == 224.0 and r["layout"].startswith(layout)
    assert r["groups"]["B"] == {"total": 116.0, "vat": 16.0}
    if groups > 16:
        assert r["groups"]["Q"] == {"total": 108.0, "vat": 8.0} and r["mt"] == 24.0


def test_open_invoice_credit_note_needs_24_chars():
    inv = {"operator": {"id": "1", "name": "J"}, "nif": "A1", "invoice_type": "FA", "pmode": "TTC",
           "isf": "ABC-DEF-01", "fn": "SINV-1", "credit_note": {"rr": "RAN", "rn": "ABCD-EFGH-IJKL-MNOP-QRST-UVWX"}}
    assert ",FA,RAN,ABCDEFGHIJKLMNOPQRSTUVWX,TTC," in P.open_invoice_data(inv)
    inv["credit_note"]["rn"] = "SHORT"
    with pytest.raises(P.ProtocolError):
        P.open_invoice_data(inv)


def test_parse_subtotal_both_layouts():
    spec = ["100.00"] + ["0"] * 16 + ["0"] * 16 + ["0", "0"]
    spec[1 + 1] = "100.00"            # MVB
    spec[17 + 1] = "13.79"            # MTB
    r = P.parse_subtotal(",".join(spec))
    assert r["layout"] == "spec35" and r["mv"] == 100 and r["mt"] == 13.79
    elt = ["100.00", "13.79"] + spec[1:]
    r = P.parse_subtotal(",".join(elt))
    assert r["layout"] == "eltrade36" and r["groups"]["B"] == {"total": 100.0, "vat": 13.79}


def test_parse_38_both_orders():
    sig = "D357MYBKF6QSMSD2XWSEFT6S"
    a = P.parse_normalized(f"42,70,FV,20250301122547,AB01000001-1,A1234567B,{sig},FAC01/2025", "FAC01/2025")
    b = P.parse_normalized(f"42,70,FV,20250301122547,AB01000001-1,A1234567B,FAC01/2025,{sig}", "FAC01/2025")
    assert a["sig"] == b["sig"] == sig and a["fn"] == b["fn"] == "FAC01/2025"
    assert P.nid_of(a["mid"]) == "AB01000001"


def test_classify():
    assert P.classify("E:ANNULEE") == ("E", "ANNULEE")
    assert P.classify("P:x,y")[0] == "P"
    assert P.classify("R0")[0] == "R"
    assert P.classify("R-400")[0] == "R"
    assert P.classify("49,49,20250301100118,DIS")[0] == "DATA"


def test_qr_and_sig_format():
    assert P.qr_content("AB01000001-1", "D357MYBKF6QSMSD2XWSEFT6S", "A0001234A", "20250301122547") == \
        "RDCDEF01;AB01000001-1;D357MYBKF6QSMSD2XWSEFT6S;A0001234A;20250301122547"
    assert P.format_sig("D357MYBKF6QSMSD2XWSEFT6S") == "D357-MYBK-F6QS-MSD2-XWSE-FT6S"
