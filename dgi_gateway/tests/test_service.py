"""Service loop: configuration received from ERPNext versus the local configuration of the site."""
import signal

import pytest

from conftest import NID, NIF
from dgi_gateway import cli
from dgi_gateway.config import GatewayConfig
from dgi_gateway.device import MCFDevice
from dgi_gateway.journal import Journal
from dgi_gateway.service import GatewayService
from dgi_gateway.simulator import MCFSimulator


class FakeServer:
    def __init__(self, **conf):
        self.conf = dict({"status": "Active", "expected_nid": NID, "expected_nif": NIF}, **conf)

    def config(self):
        return self.conf


def _service(tmp_path, local_port, **server_conf):
    opened = []

    def factory(port, nid):
        opened.append((port, nid))
        return MCFDevice(MCFSimulator(nid=NID, nif=NIF))

    cfg = GatewayConfig(site_url="https://erp.example.com", gateway_id="GW-T", serial_port=local_port)
    svc = GatewayService(cfg, FakeServer(**server_conf), Journal(str(tmp_path / "j.sqlite3")),
                         device_factory=factory, sleep=lambda s: None)
    return svc, opened


def test_empty_port_in_erpnext_keeps_the_local_port(tmp_path):
    # regression found on the installed package: an empty "Port serie" in ERPNext erased the
    # port given to 'dgi-gateway init --port', and the service probed other serial ports
    svc, opened = _service(tmp_path, "/dev/dgi-mcf", serial_port="")
    svc.refresh_config()
    assert svc.ensure_device()
    assert opened == [("/dev/dgi-mcf", NID)]


def test_port_typed_in_erpnext_wins_and_reopens(tmp_path):
    svc, opened = _service(tmp_path, "/dev/ttyACM0", serial_port="/dev/dgi-mcf", baudrate=115200)
    svc.refresh_config()
    assert svc.ensure_device()
    assert opened == [("/dev/dgi-mcf", NID)]
    svc.server.conf["serial_port"] = "/dev/ttyUSB1"
    svc.refresh_config()                 # change -> device dropped, reopened on the new port
    assert svc.ensure_device()
    assert opened[-1] == ("/dev/ttyUSB1", NID)


def test_inactive_gateway_is_refused(tmp_path):
    from dgi_gateway.api_client import ServerError
    svc, _ = _service(tmp_path, "", status="A approuver")
    with pytest.raises(ServerError):
        svc.refresh_config()


def test_sigterm_stops_the_loop_gracefully(tmp_path, monkeypatch):
    captured = {}

    class StubService:
        def __init__(self, *a, **k):
            self.stopped = False
            captured["svc"] = self

        def stop(self):
            self.stopped = True

        def run_forever(self):
            signal.raise_signal(signal.SIGTERM)

    cfg = GatewayConfig(site_url="https://erp.example.com", gateway_id="GW-T")
    monkeypatch.setattr(cli.GatewayConfig, "load", classmethod(lambda c, p=None: cfg))
    monkeypatch.setattr(cli.Identity, "load", classmethod(lambda c, g, p: object()))
    monkeypatch.setattr(cli, "ServerClient", lambda *a, **k: None)
    monkeypatch.setattr(cli, "Journal", lambda p: None)
    monkeypatch.setattr(cli, "GatewayService", StubService)
    old = signal.getsignal(signal.SIGTERM)
    try:
        cli.cmd_run(None)
    finally:
        signal.signal(signal.SIGTERM, old)
    assert captured["svc"].stopped
