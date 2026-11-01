"""Banc de test uniquement (Linux) : expose le simulateur MCF derriere un vrai pseudo-terminal (/dev/pts/N) : la
passerelle installee lui parle par pyserial comme a un port COM virtuel USB.

    python pty_bridge.py <etat.json> <fichier-port> [NID] [NIF]
puis : dgi-gateway init ... --port $(cat <fichier-port>)
"""
import json, os, pty, select, sys, tty
from dgi_gateway import protocol as P
from dgi_gateway.simulator import MCFSimulator

state, port_file = sys.argv[1], sys.argv[2]
nid = sys.argv[3] if len(sys.argv) > 3 else "E2E0000001"
nif = sys.argv[4] if len(sys.argv) > 4 else "A0700190Q"
s = MCFSimulator(nid=nid, nif=nif, rates={"A": 0, "B": 16, "C": 5, "O": 1, "P": 1})
if os.path.exists(state):
    st = json.load(open(state)); s.tc, s.fvc, s.frc, s.last = st["tc"], st["fvc"], st["frc"], st["last"]
m, sl = pty.openpty()
tty.setraw(sl)
open(port_file, "w").write(os.ttyname(sl))
buf, frames = b"", 0
while True:
    r, _, _ = select.select([m], [], [], 0.5)
    if not r:
        continue
    buf += os.read(m, 4096)
    while bytes([P.ETX]) in buf:
        i = buf.index(bytes([P.ETX]))
        frame, buf = buf[:i + 1], buf[i + 1:]
        j = frame.rfind(bytes([P.SOH]))
        if j < 0:
            continue
        s.write(frame[j:])
        os.write(m, s.read(1 << 16, 0))
        frames += 1
        json.dump({"tc": s.tc, "fvc": s.fvc, "frc": s.frc, "last": s.last}, open(state, "w"))
        print("frame", frames, "cmd", hex(frame[j + 3]), "tc", s.tc, flush=True)
