# -*- coding: utf-8 -*-
"""Tests de l'agent MCF (sans materiel): framing exact du protocole MCF-SFE v1.0,
echappement, NAK/SYN, flux complet de facture sur un MCF simule, et garde-fous.
Executer: python test_mcf_agent.py"""



import mcf_agent as A


# ------------------------------------------------------------ framing
def make_response_frame(seq, cmd, data=b"", sta=b"\x80" * 6):
    core = bytes([0x20 + 3 + len(data) + 1 + 6 + 1, seq, cmd]) + data + bytes([A.BRK]) + sta + bytes([A.AMB])
    return bytes([A.SOH]) + core + A.bcc16(core) + bytes([A.ETX])


def test_bcc_doc_example():
    # PDF 3.2: une somme de 12ABh est transmise comme 31h 32h 3Ah 3Bh
    core = bytes([255] * 18 + [189])  # sum = 4779 = 0x12AB
    assert sum(core) & 0xFFFF == 0x12AB
    assert A.bcc16(core) == b"\x31\x32\x3a\x3b"


def test_build_frame_structure():
    f = A.build_frame(0x21, A.CMD_STATUS, b"")
    assert f[0] == A.SOH and f[-1] == A.ETX
    assert f[1] == 0x20 + 4          # LEN..AMB = 4 quand DATA vide
    assert f[2] == 0x21 and f[3] == A.CMD_STATUS and f[4] == A.AMB
    assert A.bcc16(f[1:-5]) == f[-5:-1]
    data = b"1123,Jan,00001234A,FV,TTC,SFEABC001,FAC05/2025"
    f2 = A.build_frame(0x22, A.CMD_OPEN, data)
    assert f2[1] == 0x20 + 4 + len(data)


def test_parse_response_roundtrip():
    frame = make_response_frame(0x25, A.CMD_STATUS, b"AB01000001-1,A1234567B", b"\x80\x80\x88\x80\x80\x80")
    p = A.parse_response(frame)
    assert p["seq"] == 0x25 and p["cmd"] == A.CMD_STATUS
    assert p["data"] == b"AB01000001-1,A1234567B"
    assert A.invoice_is_open(p["sta"])  # octet 3, bit 3 = facture ouverte
    # BCC corrompu -> erreur
    bad = frame[:-3] + bytes([frame[-3] ^ 1]) + frame[-2:]
    try:
        A.parse_response(bad)
        raise AssertionError("BCC corrompu accepte")
    except A.ProtocolError:
        pass


def test_escaping():
    s = "A,B <C> & D\r\n"
    e = A.escape_text(s)
    assert "," not in e and "<" not in e and ">" not in e and "\r" not in e and "\n" not in e
    assert A.unescape_text(e) == s


# ------------------------------------------------------------ MCF simule
class FakeSerial:
    """Simule le port serie: script = liste de reponses par commande recue."""

    def __init__(self, script):
        self.script = script      # list of (expected_cmd or None, responder)
        self.rx = b""
        self.sent = []

    def write(self, frame):
        self.sent.append(frame)
        cmd = frame[3]
        data = frame[4:-6].decode("utf-8", "replace")
        seq = frame[2]
        if not self.script:
            raise AssertionError(f"commande inattendue {cmd:02X}h")
        expected, responder = self.script.pop(0)
        if expected is not None:
            assert cmd == expected, f"attendu {expected:02X}h recu {cmd:02X}h"
        out = responder(seq, cmd, data) if callable(responder) else responder
        if isinstance(out, str):
            out = make_response_frame(seq, cmd, out.encode("utf-8"))
        elif isinstance(out, int):
            out = bytes([out])
        self.rx += out

    def read(self, n=1):
        chunk, self.rx = self.rx[:n], self.rx[n:]
        return chunk

    def reset_input_buffer(self):
        pass

    def flush(self):
        pass

    def close(self):
        pass


def make_device(script):
    dev = A.MCFDevice.__new__(A.MCFDevice)
    dev.ser = FakeSerial(script)
    dev.port = "SIM"
    dev._seq = 0x20
    return dev


C1_RESP = ("AB01000001-1,A1234567B,20250301100320,49,46,3,20250301100305,FV,"
           "QWERTY,FAC05/2025,13000.00,"
           "0.00,16.00,0.00,0.00,0.00,0.00,0.00,0.00,0.00,0.00,0.00,0.00,0.00,0.00,0.00,0.00")


def test_nak_retransmission_and_syn():
    # 1er envoi -> NAK, 2e envoi -> SYN puis trame valide
    responses = [
        (A.CMD_STATUS, A.NAK),
        (A.CMD_STATUS, lambda seq, cmd, data: bytes([A.SYN]) + make_response_frame(seq, cmd, C1_RESP.encode())),
    ]
    dev = make_device(responses)
    st = dev.status()
    assert st["MID"] == "AB01000001-1" and st["NIF"] == "A1234567B"
    assert st["TAXB"] == "16.00"
    assert len(dev.ser.sent) == 2
    assert dev.ser.sent[0][2] == dev.ser.sent[1][2], "NAK => meme SEQ retransmis"


PAYLOAD = {
    "invoice": "ACC-SINV-0001", "fn": "ACC-SINV-0001",
    "nif": "A1234567B", "isf": "AB01710090", "pmode": "TTC", "invoice_type": "FV",
    "operator": {"id": "badi", "name": "Badi"},
    "client": {"ctype": "PM", "cnif": "B1234567C", "cnom": "ABC SARL", "cadd": "BLVD 23",
               "ctel": "3488772", "ceml": "info@abc.cd", "crccm": "RCCM2025"},
    "items": [
        {"name": "EAU", "itype": "BIE", "tax_group": "A", "tax_rate": 0.0,
         "amount": 3600.0, "price": 720.0, "qty": 5},
        {"name": "LAIT, GR", "itype": "BIE", "tax_group": "B", "tax_rate": 16.0,
         "amount": 464.0, "price": 464.0, "qty": 1},
    ],
    "payments": [{"letter": "E", "amount": 4064.0}],
    "comments": {"A": "Livraison <urgente>"},
    "totals": {"mv": 4064.0, "mt": 64.0},
}

SUBTOTAL_RESP = ("4064.00,3600.00,464.00,0,0,0,0,0,0,0,0,0,0,0,0,0,0,"
                 "0,64.00,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0")
# ordre "exemple officiel": ...,NIF,SIG,FN
FINAL_RESP = "42,70,FV,20250301122547,AB01000001-1,A1234567B,D357MYBKF6QSMSD2XWSEFT6S,ACC-SINV-0001"


def full_script(c1=C1_RESP, final=FINAL_RESP, open_resp="24,50,,,,,,"):
    return [
        (A.CMD_STATUS, c1),
        (A.CMD_CLIENT, "R"),
        (A.CMD_OPEN, open_resp),
        (A.CMD_ITEM, "3600.00,3600.00,0.00,0.00"),
        (A.CMD_ITEM, "464.00,400.00,64.00,0.00"),
        (A.CMD_INFO, "R"),
        (A.CMD_SUBTOTAL, SUBTOTAL_RESP),
        (A.CMD_PAY, "R0"),
        (A.CMD_FINALIZE, final),
    ]


def test_run_job_success():
    dev = make_device(full_script())
    res = A.run_job(dev, PAYLOAD, tolerance=1.0)
    assert res["sig"] == "D357MYBKF6QSMSD2XWSEFT6S"
    assert res["fn"] == "ACC-SINV-0001"                    # FN/SIG resolus malgre l'ordre inverse
    assert (res["fc"], res["tc"], res["ft"]) == ("42", "70", "FV")
    assert res["machine_mv"] == 4064.0 and res["machine_mt"] == 64.0
    # la commande 31h du 2e article doit contenir la virgule echappee et le groupe B
    item2 = dev.ser.sent[4][4:-6].decode()
    assert "LAIT^x2c; GR" in item2 and "\tBIE,B16.00%464.00\t464.00" in item2
    # C0h: OPID,OPN,NIF,FV,TTC,ISF,FN  (sent[2]: C1=0, C3=1, C0=2)
    c0 = dev.ser.sent[2][4:-6].decode()
    assert c0 == "badi,Badi,A1234567B,FV,TTC,AB01710090,ACC-SINV-0001"


def test_run_job_fn_sig_spec_order():
    final = "42,70,FV,20250301122547,AB01000001-1,A1234567B,ACC-SINV-0001,D357MYBKF6QSMSD2XWSEFT6S"
    dev = make_device(full_script(final=final))
    res = A.run_job(dev, PAYLOAD, tolerance=1.0)
    assert res["fn"] == "ACC-SINV-0001" and res["sig"] == "D357MYBKF6QSMSD2XWSEFT6S"


def test_run_job_wrong_nif_blocks():
    bad = dict(PAYLOAD, nif="X9999999Z")
    dev = make_device([(A.CMD_STATUS, C1_RESP)])
    try:
        A.run_job(dev, bad, tolerance=1.0)
        raise AssertionError("NIF errone accepte")
    except A.DeviceError as e:
        assert "NIF" in str(e)


def test_run_job_rate_mismatch_blocks():
    bad = {**PAYLOAD, "items": [dict(PAYLOAD["items"][1], tax_rate=18.0)]}
    dev = make_device([(A.CMD_STATUS, C1_RESP)])
    try:
        A.run_job(dev, bad, tolerance=1.0)
        raise AssertionError("taux divergent accepte")
    except A.DeviceError as e:
        assert "groupe B" in str(e)


def test_run_job_subtotal_mismatch_cancels():
    bad_sub = SUBTOTAL_RESP.replace("4064.00", "4100.00", 1)
    script = full_script()
    script[6] = (A.CMD_SUBTOTAL, bad_sub)
    script[7] = (A.CMD_FINALIZE, "E:ANNULEE")   # annulation de securite attendue
    script = script[:8]
    dev = make_device(script)
    try:
        A.run_job(dev, PAYLOAD, tolerance=1.0)
        raise AssertionError("ecart 33h accepte")
    except A.DeviceError as e:
        assert "33h" in str(e)
    assert dev.ser.sent[-1][4:-6] == b"C", "la facture ouverte doit etre annulee (38h C)"


def test_open_invoice_credit_note_and_usd():
    p = dict(PAYLOAD, invoice_type="FA",
             credit_note={"rr": "COR", "rn": "ABCDABCDABCDABCDABCDABCD"},
             exchange={"crt": 2850.0, "cdt": "20260705"})
    dev = make_device([(A.CMD_OPEN, "1,51,,,,,,")])
    dev.open_invoice(p)
    c0 = dev.ser.sent[0][4:-6].decode()
    assert c0 == ("badi,Badi,A1234567B,FA,COR,ABCDABCDABCDABCDABCDABCD,"
                  "TTC,AB01710090,ACC-SINV-0001,2850.00,20260705")


def test_open_invoice_open_receipt_flag_then_cancel():
    c1_open = C1_RESP  # flags via STA: on simule 'facture ouverte' dans la trame C1
    script = [
        (A.CMD_STATUS, lambda seq, cmd, data: make_response_frame(
            seq, cmd, c1_open.encode(), sta=b"\x80\x80\x88\x80\x80\x80")),
        (A.CMD_FINALIZE, "E:ANNULEE"),           # annulation de securite
        (A.CMD_CLIENT, "R"),
        (A.CMD_OPEN, "24,50,,,,,,"),
        (A.CMD_ITEM, "3600.00,3600.00,0.00,0.00"),
        (A.CMD_ITEM, "464.00,400.00,64.00,0.00"),
        (A.CMD_INFO, "R"),
        (A.CMD_SUBTOTAL, SUBTOTAL_RESP),
        (A.CMD_PAY, "R0"),
        (A.CMD_FINALIZE, FINAL_RESP),
    ]
    dev = make_device(script)
    res = A.run_job(dev, PAYLOAD, tolerance=1.0)
    assert res["sig"].startswith("D357")


# ------------------------------------------------------------ main
def main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"OK  {fn.__name__}")
    print(f"\n{len(fns)} tests OK")


if __name__ == "__main__":
    main()
