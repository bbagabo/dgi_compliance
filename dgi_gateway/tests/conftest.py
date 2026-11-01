import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dgi_gateway.device import MCFDevice  # noqa: E402
from dgi_gateway.journal import Journal  # noqa: E402
from dgi_gateway.simulator import MCFSimulator  # noqa: E402

NID, NIF = "SIM0000001", "A0700190Q"


def make_op(op_id="OP-1", fn="SINV-0001", *, itype="FV", items=None, payments=None, totals=None,
            pmode="TTC", credit_note=None, exchange=None, nid=NID, nif=NIF):
    items = items or [{"name": "Lait en poudre", "itype": "BIE", "tax_group": "B", "tax_rate": 16,
                       "amount": 2320, "price": 1160, "qty": 2},
                      {"name": "Eau", "itype": "BIE", "tax_group": "A", "tax_rate": 0,
                       "amount": 500, "price": 500, "qty": 1}]
    inv = {"fn": fn, "nif": nif, "isf": "ABC-DEF-01", "invoice_type": itype, "pmode": pmode,
           "operator": {"id": "caisse2", "name": "Jeanne"}, "client": {"ctype": "PP"},
           "items": items, "payments": payments if payments is not None else [{"letter": "E", "amount": 2820}],
           "totals": totals or {"mv": 2820, "mt": 320}, "comments": {"A": "Merci"}}
    if credit_note:
        inv["credit_note"] = credit_note
    if exchange:
        inv["exchange"] = exchange
    return {"operation_id": op_id, "op_type": "NORMALIZE", "lease_token": "tok-" + op_id,
            "device": {"nid": nid, "nif": nif}, "invoice": inv}


@pytest.fixture
def sim():
    return MCFSimulator(nid=NID, nif=NIF)


@pytest.fixture
def device(sim):
    return MCFDevice(sim, response_timeout=0.5)


@pytest.fixture
def journal(tmp_path):
    return Journal(str(tmp_path / "journal.sqlite3"))
