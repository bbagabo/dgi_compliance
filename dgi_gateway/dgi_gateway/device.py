"""Exchanges with ONE MCF over a byte transport (serial port, USB virtual COM, or simulator).

Retransmission rules (protocol 3.3):
* NAK -> resend the SAME frame (same SEQ);
* no answer -> resend the SAME frame: if the MCF had received it, it answers again with its
  last response without executing the command twice (same SEQ + same CMD, 3.3.2);
* SYN -> the MCF needs more time: the deadline is extended (bounded by max_syn_wait);
* a NEW command always uses a NEW SEQ, even after a failure (otherwise two identical 31h lines
  would be answered from the cache without being recorded).
"""
import logging
import time

from . import protocol as P

log = logging.getLogger("dgi_gateway.device")


class LinkError(Exception):
    """The MCF cannot be reached (port missing, cable unplugged, no answer)."""


class DeviceRefused(Exception):
    """The MCF answered E:... (definitive refusal of a command)."""

    def __init__(self, command: str, device_text: str, code: str = "REFUS_MCF"):
        self.command, self.device_text, self.code = command, device_text, code
        super().__init__(f"{command} : {device_text}")


class Transport:
    """Minimal byte transport interface."""

    name = "?"

    def write(self, data: bytes) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def read(self, size: int, timeout: float) -> bytes:  # pragma: no cover - interface
        raise NotImplementedError

    def reset_input(self) -> None:
        pass

    def close(self) -> None:
        pass


class SerialTransport(Transport):
    def __init__(self, port: str, baudrate: int = 115200):
        try:
            import serial  # pyserial
        except ImportError as e:  # pragma: no cover
            raise LinkError("pyserial n'est pas installe") from e
        try:
            self.ser = serial.Serial(port, baudrate, bytesize=8, parity="N", stopbits=1,
                                     timeout=0.05, write_timeout=2, exclusive=True)
        except Exception as e:
            raise LinkError(f"ouverture du port {port} impossible : {e}") from e
        self.name = port

    def write(self, data: bytes) -> None:
        try:
            self.ser.write(data)
            self.ser.flush()
        except Exception as e:
            raise LinkError(f"ecriture sur {self.name} impossible : {e}") from e

    def read(self, size: int, timeout: float) -> bytes:
        try:
            self.ser.timeout = max(0.01, timeout)
            return self.ser.read(size)
        except Exception as e:
            raise LinkError(f"lecture sur {self.name} impossible : {e}") from e

    def reset_input(self) -> None:
        try:
            self.ser.reset_input_buffer()
        except Exception:
            pass

    def close(self) -> None:
        try:
            self.ser.close()
        except Exception:
            pass


def list_serial_ports() -> list[dict]:
    try:
        from serial.tools import list_ports
    except ImportError:  # pragma: no cover
        return []
    out = []
    for p in list_ports.comports():
        out.append({"port": p.device, "vid": p.vid, "pid": p.pid, "description": p.description or ""})
    return out


class MCFDevice:
    def __init__(self, transport: Transport, *, response_timeout: float = 0.6, retries: int = 3,
                 max_syn_wait: float = 15.0, start_seq: int = P.SEQ_MIN):
        self.t = transport
        self.response_timeout = max(0.5, response_timeout)   # SFE must wait at least 500 ms (3.1)
        self.retries = retries
        self.max_syn_wait = max_syn_wait
        self._seq = start_seq if P.SEQ_MIN <= start_seq <= P.SEQ_MAX else P.SEQ_MIN
        self.last_sta = b""
        self.group_count = None  # number of tax groups announced by C1h (16 in protocol v1.0, more after update)
        self.exchanges = []      # short audit trail of the current operation

    @property
    def port(self) -> str:
        return getattr(self.t, "name", "?")

    def close(self):
        self.t.close()

    # ------------------------------------------------------------------ low level
    def _read_frame(self, timeout: float):
        """Return 'NAK', a complete frame (bytes) or None on timeout. SYN extends the wait."""
        deadline = time.monotonic() + timeout
        syn_limit = time.monotonic() + self.max_syn_wait
        buf = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            chunk = self.t.read(1, min(remaining, 0.2))
            if not chunk:
                continue
            b = chunk[0]
            if not buf:
                if b == P.NAK:
                    return "NAK"
                if b == P.SYN:
                    deadline = min(syn_limit, max(deadline, time.monotonic() + 0.5))
                    continue
                if b != P.SOH:
                    continue
            buf.append(b)
            if b == P.ETX and len(buf) >= 14:
                return bytes(buf)

    def transact(self, cmd: int, data: str = "", *, timeout: float | None = None) -> P.Response:
        payload = data.encode("utf-8") if isinstance(data, str) else (data or b"")
        if len(payload) > P.MAX_DATA:
            raise P.ProtocolError(f"{P.COMMAND_NAMES.get(cmd, hex(cmd))} : {len(payload)} octets, "
                                  f"le MCF en accepte {P.MAX_DATA} au maximum (COMMANDE_TROP_LONGUE)")
        self._seq = P.next_seq(self._seq)
        seq = self._seq
        frame = P.build_frame(seq, cmd, payload)
        timeout = timeout or self.response_timeout
        last_problem = "pas de reponse"
        for attempt in range(1, self.retries + 1):
            self.t.reset_input()
            self.t.write(frame)
            reply = self._read_frame(timeout)
            if reply == "NAK":
                last_problem = "NAK"
                log.warning("NAK sur %s (essai %s) : renvoi avec le meme SEQ", P.COMMAND_NAMES.get(cmd), attempt)
                continue
            if reply is None:
                last_problem = "pas de reponse"
                log.warning("pas de reponse a %s (essai %s)", P.COMMAND_NAMES.get(cmd), attempt)
                continue
            try:
                resp = P.parse_response_frame(reply)
            except P.ProtocolError as e:
                last_problem = str(e)
                log.warning("trame invalide (%s) : renvoi", e)
                continue
            if resp.seq != seq or resp.cmd != cmd:
                last_problem = "reponse d'un autre echange"
                continue
            self.last_sta = resp.sta
            self.exchanges.append({"cmd": P.COMMAND_NAMES.get(cmd, hex(cmd)), "reply": resp.text[:80],
                                   "flags": P.status_flags(resp.sta)})
            return resp
        raise LinkError(f"le MCF ne repond pas a {P.COMMAND_NAMES.get(cmd, hex(cmd))} sur {self.port} ({last_problem})")

    def _checked(self, cmd: int, data: str = "", *, timeout: float | None = None, label: str | None = None) -> str:
        resp = self.transact(cmd, data, timeout=timeout)
        kind, payload = P.classify(resp.text)
        if kind == "E":
            raise DeviceRefused(label or P.COMMAND_NAMES.get(cmd, hex(cmd)), payload or resp.text)
        return resp.text

    # ------------------------------------------------------------------ commands
    def status(self) -> dict:
        resp = self.transact(P.CMD_STATUS)
        st = P.parse_status(resp.text)
        st["flags"] = P.status_flags(resp.sta)
        st["invoice_open"] = P.invoice_open(resp.sta)
        if st.get("group_count"):
            self.group_count = st["group_count"]
        return st

    def server_status(self, restart: bool = False) -> dict:
        resp = self.transact(P.CMD_SERVER, "RESTART" if restart else "", timeout=3.0)
        return P.parse_server_status(resp.text)

    def taxpayer(self, opt: str) -> str:
        return P.unescape_text(self.transact(P.CMD_TAXPAYER, opt).text)

    def set_client(self, client: dict | None) -> None:
        self._checked(P.CMD_CLIENT, P.client_data(client))

    def open_invoice(self, inv: dict) -> dict:
        return P.parse_open_invoice(self._checked(P.CMD_OPEN, P.open_invoice_data(inv), timeout=3.0))

    def add_item(self, it: dict) -> str:
        return self._checked(P.CMD_ITEM, P.item_data(it), timeout=3.0,
                             label="31h article « %s »" % str(it.get("name") or "")[:24])

    def set_comment(self, line_id: str, content: str) -> None:
        self._checked(P.CMD_INFO, P.comment_data(line_id, content))

    def subtotal(self) -> dict:
        return P.parse_subtotal(self._checked(P.CMD_SUBTOTAL, timeout=3.0), self.group_count)

    def pay(self, letter: str | None = None, amount=None) -> float:
        return P.parse_payment(self._checked(P.CMD_PAYMENT, P.payment_data(letter, amount), timeout=3.0))

    def finalize(self, mv, mt, *, expected_fn: str | None = None, pending_timeout: float = 90.0,
                 pending_interval: float = 1.5, on_pending=None) -> dict:
        """38h. A P: answer means the MCF waits for the DGI server: resend the same command
        (protocol 4.4, 38h) until R: or E:, at most pending_timeout seconds."""
        data = P.finalize_data(mv, mt)
        deadline = time.monotonic() + pending_timeout
        while True:
            resp = self.transact(P.CMD_FINALIZE, data, timeout=5.0)
            kind, payload = P.classify(resp.text)
            if kind == "E":
                raise DeviceRefused("38h normalisation", payload)
            if kind == "P":
                if on_pending:
                    on_pending(payload)
                if time.monotonic() >= deadline:
                    raise PendingTimeout(payload)
                time.sleep(pending_interval)
                continue
            return P.parse_normalized(payload if kind == "R" else resp.text, expected_fn=expected_fn)

    def cancel(self) -> str:
        """38h with C: cancels the invoice being normalized (expected answer E:ANNULEE)."""
        resp = self.transact(P.CMD_FINALIZE, "C", timeout=3.0)
        return resp.text


class PendingTimeout(Exception):
    """38h still answers P: after the allowed time (MCF waiting for the DGI server)."""
