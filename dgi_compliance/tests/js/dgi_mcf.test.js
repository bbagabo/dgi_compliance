/*
 * Tests of public/js/dgi_mcf.js (Poste MCF) against the reference Python implementation.
 *   node --test dgi_compliance/tests/js/
 * The MCF is the Python simulator of dgi_gateway (same as the gateway and Frappe tests), reached
 * through tests/js/sim_bridge.py. PYTHON env var: interpreter with frappe + cryptography (bench env).
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { spawn } = require("node:child_process");
const readline = require("node:readline");
const M = require(path.join(__dirname, "..", "..", "public", "js", "dgi_mcf.js"));

// ------------------------------------------------------------------ bridge
const py = spawn(process.env.PYTHON || "python3", [path.join(__dirname, "sim_bridge.py")], { stdio: ["pipe", "pipe", "inherit"] });
const rl = readline.createInterface({ input: py.stdout });
const waiting = [];
rl.on("line", (line) => waiting.shift()(JSON.parse(line)));
function ask(req) {
	return new Promise((resolve) => { waiting.push(resolve); py.stdin.write(JSON.stringify(req) + "\n"); });
}
test.after(() => py.kill());

const toHex = (b) => Buffer.from(b).toString("hex");
let simCount = 0;
class SimTransport {
	constructor(id) { this.id = id; this.buf = []; this.name = "SIM"; }
	async write(bytes) {
		const r = await ask({ op: "write", id: this.id, hex: toHex(bytes) });
		if (r.error) throw new M.LinkError(r.error);
		for (const b of Buffer.from(r.hex, "hex")) this.buf.push(b);
	}
	async read(n, timeout) {
		if (!this.buf.length) { await new Promise((r) => setTimeout(r, Math.min(timeout, 20))); return new Uint8Array(0); }
		return Uint8Array.from(this.buf.splice(0, n));
	}
	resetInput() { this.buf.length = 0; }
	async close() {}
}
async function newSim(opts = {}) {
	const id = "s" + ++simCount;
	await ask(Object.assign({ op: "new", id, nid: "SIM0000001", nif: "A0700190Q" }, opts));
	return { id, dev: new M.MCFDevice(new SimTransport(id), { responseTimeout: 500 }),
		fault: (name, value) => ask({ op: "fault", id, name, value }), state: () => ask({ op: "state", id }) };
}
function makeOp(id = "OP-1", fn = "SINV-0001", extra = {}) {
	const inv = Object.assign({
		fn, nif: "A0700190Q", isf: "ABC-DEF-01", invoice_type: "FV", pmode: "TTC",
		operator: { id: "caisse2", name: "Jeanne" }, client: { ctype: "PP" },
		items: [{ name: "Lait en poudre", itype: "BIE", tax_group: "B", tax_rate: 16, amount: 2320, price: 1160, qty: 2 },
			{ name: "Eau", itype: "BIE", tax_group: "A", tax_rate: 0, amount: 500, price: 500, qty: 1 }],
		payments: [{ letter: "E", amount: 2820 }], totals: { mv: 2820, mt: 320 }, comments: { A: "Merci" },
	}, extra);
	return { operation_id: id, op_type: "NORMALIZE", lease_token: "tok-" + id, device: { nid: "SIM0000001", nif: "A0700190Q" }, invoice: inv };
}

// ------------------------------------------------------------------ protocol parity with protocol.py
const PARITY = [
	["escape_text", ["Café, <b> & co\r\n\tfin\x01"]],
	["item_data", [{ name: "Lait, entier", tax_group: "B", tax_rate: 16, amount: 2320, price: 1160, qty: 2 }]],
	["item_data", [{ name: "Gasoil", desc: "CODE-1", itype: "BIE", tax_group: "C", tax_rate: 5, amount: 99.9, price: 33.3, qty: 3,
		ts_value: "10%", ts_total: 9.99, ts_desc: "TS carburant" }]],
	["item_data", [{ name: "Promo", tax_group: "Q", tax_rate: 8, amount: 90, price: 90, orig_price: 100, price_desc: "Remise 10 %" }]],
	["item_data", [{ name: "Riz 1,5 kg", tax_group: "S", tax_rate: 0, amount: 1.5, price: 1, qty: 1.5 }]],
	["open_invoice_data", [{ operator: { id: "1", name: "J" }, nif: "A1", invoice_type: "FV", pmode: "ht", isf: "ABC-DEF-01", fn: "SINV-1",
		exchange: { crt: 2850.5, cdt: "20261031" } }]],
	["open_invoice_data", [{ operator: { id: "1", name: "J" }, nif: "A1", invoice_type: "FA", pmode: "TTC", isf: "ABC-DEF-01", fn: "SINV-2",
		credit_note: { rr: "RAN", rn: "ABCD-EFGH-IJKL-MNOP-QRST-UVWX" } }]],
	["client_data", [{ ctype: "PM", cnif: "A0700190Q", cnom: "CLIENT, SARL", cadd: "Av. <Kasa-Vubu>", ctel: "+243", ceml: "a@b.cd", crccm: "CD/KIN" }]],
	["payment_data", ["R", 1080]], ["payment_data", ["m", 12.345]], ["finalize_data", [2820, 320.004]],
	["parse_status", ["AB01000001-1,A1234567B,20250301100320,49,46,3,20250301100305,FV,QWERTYUIOPLKJHGFDSAZXCVB,FAC05/2025,13000.00,0.00,16.00,8.00,0.00,0.00,16.00,8.00,0.00,0.00,0.00,0.00,0.00,0.00,0.00,0.00,0.00,0.00,8.00,0.00,0.00"]],
	["parse_normalized", ["1,50,FV,20261031101010,SIM0000001-1,A0700190Q,QWERTYUIOPLKJHGFDSAZXCVB,SINV-1", "SINV-1"]],
	["parse_normalized", ["1,50,FV,20261031101010,SIM0000001-1,A0700190Q,SINV-1,qwertyuioplkjhgfdsazxcvb"]],
	["classify", ["P:ATTENTE SERVEUR"]], ["classify", ["R-12.50"]], ["classify", ["E"]],
	["fmt_qty", [1.5]], ["fmt_qty", [2]], ["fmt_amount", [-0.004]], ["truncate_utf8", ["é^amp;é^amp;", 5]],
];
const JS_NAMES = {
	escape_text: M.escapeText, item_data: M.itemData, open_invoice_data: M.openInvoiceData, client_data: M.clientData,
	payment_data: M.paymentData, finalize_data: M.finalizeData, parse_status: M.parseStatus,
	parse_normalized: M.parseNormalized, classify: M.classify, fmt_qty: M.fmtQty, fmt_amount: M.fmtAmount,
	truncate_utf8: M.truncateUtf8,
};
test("protocol: same output as protocol.py", async () => {
	for (const [fn, args] of PARITY) {
		const expected = await ask({ op: "py", fn, args });
		assert.ok(!expected.error, `${fn} python: ${expected.message}`);
		const got = JS_NAMES[fn](...args);
		assert.deepEqual(JSON.parse(JSON.stringify(got)), expected.value, `${fn}(${JSON.stringify(args)})`);
	}
});

test("protocol: frames and refusals", async () => {
	for (const [seq, cmd, data] of [[0x21, M.CMD.STATUS, ""], [0xff, M.CMD.ITEM, M.itemData({ name: "Thé, sucré", tax_group: "B", tax_rate: 16, amount: 1.16, price: 1.16 })],
		[0x20, M.CMD.FINALIZE, "C"]]) {
		const js = M.buildFrame(seq, cmd, new TextEncoder().encode(data));
		assert.equal(toHex(js), (await ask({ op: "frame", seq, cmd, data })).hex);
	}
	assert.throws(() => M.itemData({ name: "X", tax_group: "1", tax_rate: 0, amount: 1, price: 1 }), M.ProtocolError);
	assert.throws(() => M.paymentData("RR", 1), M.ProtocolError);
	const sub = Array(19).fill("0.00"), vat = Array(19).fill("0.00");
	sub[16] = "108.00"; vat[16] = "8.00";
	const r = M.parseSubtotal(["108.00", "8.00", ...sub, ...vat, "0.00", "0"].join(","), 19);
	assert.equal(r.group_count, 19); assert.deepEqual(r.groups.Q, { total: 108, vat: 8 });
});

// ------------------------------------------------------------------ executor scenarios (fiscalize.py)
test("normalizes an invoice on the simulator", async () => {
	const s = await newSim();
	const res = await new M.Executor(s.dev, new M.MemoryJournal()).execute(makeOp());
	assert.equal(res.outcome, "normalized", JSON.stringify(res.error));
	assert.match(res.data.sig, /^[A-Z0-9]{24}$/);
	assert.ok(res.data.qr.startsWith("RDCDEF01;SIM0000001-1;"));
	const st = await s.state();
	assert.equal(st.invoices.length, 1); assert.equal(st.invoices[0].fn, "SINV-0001");
});

test("lost 38h answer: retransmitted, never executed twice", async () => {
	const s = await newSim();
	await s.fault("lose_response_for", [0x38]);
	const res = await new M.Executor(s.dev, new M.MemoryJournal()).execute(makeOp());
	assert.equal(res.outcome, "normalized");
	const st = await s.state();
	assert.equal(st.invoices.length, 1);
	assert.equal(st.executed.filter((c) => c === 0x38).length, 1);
});

test("NAK and SYN are handled", async () => {
	const s = await newSim();
	await s.fault("nak_next", 2); await s.fault("syn_before", 3);
	const res = await new M.Executor(s.dev, new M.MemoryJournal()).execute(makeOp());
	assert.equal(res.outcome, "normalized");
});

test("unplugged during 38h, then recovered through C1h without double", async () => {
	const s = await newSim();
	const journal = new M.MemoryJournal();
	const ex = new M.Executor(s.dev, journal);
	await s.fault("lose_response_for", [0x38]);
	await s.fault("nak_next", 0);
	// cable removed right after 38h was executed: answer lost, then every exchange fails
	const realWrite = s.dev.t.write.bind(s.dev.t);
	s.dev.t.write = async (bytes) => {
		await realWrite(bytes);
		if (bytes[3] === 0x38) await s.fault("unplugged", true);
	};
	const res = await ex.execute(makeOp());
	assert.equal(res.outcome, "uncertain");
	assert.equal((await journal.openOperations()).length, 1);
	await s.fault("unplugged", false);
	s.dev.t.write = realWrite;
	const station = new M.Station({ server: null, journal, device: s.dev });
	assert.equal(await station.reconcileJournal(), true);
	const entry = await journal.get("OP-1");
	assert.equal(entry.state, "DONE"); assert.equal(entry.result.data.source, "C1h");
	assert.equal((await s.state()).invoices.length, 1);
});

test("wrong MCF is refused before any invoice command", async () => {
	const s = await newSim({ nid: "OTHER00001" });
	const res = await new M.Executor(s.dev, new M.MemoryJournal()).execute(makeOp());
	assert.equal(res.outcome, "rejected"); assert.equal(res.error.code, "NID_INATTENDU");
	assert.ok(!(await s.state()).executed.includes(0xc0));
});

test("totals mismatch cancels the invoice on the MCF", async () => {
	const s = await newSim();
	const res = await new M.Executor(s.dev, new M.MemoryJournal()).execute(makeOp("OP-1", "SINV-0001", { totals: { mv: 2900, mt: 320 } }));
	assert.equal(res.error.code, "ECART_TOTAUX"); assert.equal(res.proven_not_normalized, true);
	const st = await s.state();
	assert.equal(st.invoices.length, 0); assert.equal(st.open, false);
});

test("P: answers then success", async () => {
	const s = await newSim();
	await s.fault("pending_38", 2);
	const ex = new M.Executor(s.dev, new M.MemoryJournal(), { pendingInterval: 10 });
	assert.equal((await ex.execute(makeOp())).outcome, "normalized");
});

test("H6: updated MCF with group Q and H5: credit letter", async () => {
	const s = await newSim({ groups: 19, rates: { B: 16, Q: 8 }, extra_payment_letters: ["R"] });
	const op = makeOp("OP-Q", "SINV-Q", { items: [{ name: "Article Q", itype: "BIE", tax_group: "Q", tax_rate: 8, amount: 1080, price: 1080, qty: 1 }],
		payments: [{ letter: "R", amount: 1080 }], totals: { mv: 1080, mt: 80 }, comments: {} });
	const res = await new M.Executor(s.dev, new M.MemoryJournal()).execute(op);
	assert.equal(res.outcome, "normalized", JSON.stringify(res.error));
});

test("H6: MCF not updated refuses group Q before opening an invoice", async () => {
	const s = await newSim();
	const op = makeOp("OP-Q2", "SINV-Q2", { items: [{ name: "Article Q", itype: "BIE", tax_group: "Q", tax_rate: 8, amount: 1080, price: 1080, qty: 1 }],
		totals: { mv: 1080, mt: 80 } });
	const res = await new M.Executor(s.dev, new M.MemoryJournal()).execute(op);
	assert.equal(res.error.code, "GROUPE_INCONNU_MCF");
	assert.ok(!(await s.state()).executed.includes(0xc0));
});

test("same lease twice returns the stored result", async () => {
	const s = await newSim();
	const ex = new M.Executor(s.dev, new M.MemoryJournal());
	const a = await ex.execute(makeOp()), b = await ex.execute(makeOp());
	assert.equal(a.data.sig, b.data.sig);
	assert.equal((await s.state()).invoices.length, 1);
});

test("journal keeps no customer data", async () => {
	const s = await newSim();
	const journal = new M.MemoryJournal();
	const op = makeOp(); op.invoice.client = { ctype: "PM", cnom: "SECRET SARL", cnif: "A999" };
	await new M.Executor(s.dev, journal).execute(op);
	assert.ok(!JSON.stringify(await journal.all()).includes("SECRET"));
});

// ------------------------------------------------------------------ signature accepted by the server code
test("WebCrypto ECDSA P-256 signature is verified by gateway.auth (and tampering is not)", async () => {
	const id = await M.Identity.create("GW-WEB-01", null);
	const fp = await id.fingerprint();
	assert.equal(fp, (await ask({ op: "fingerprint", pub: id.publicKeyB64 })).value);
	const body = Buffer.from(JSON.stringify({ snapshot: { tc: 1 } }));
	const pathName = "/api/method/dgi_compliance.gateway.api.heartbeat";
	const h = await id.signHeaders("POST", pathName, body);
	const base = { op: "verify", pub: id.publicKeyB64, path: pathName, gw: "GW-WEB-01", ts: h["X-DGI-Timestamp"],
		nonce: h["X-DGI-Nonce"], sig: h["X-DGI-Signature"], alg: "ECDSA-P256" };
	assert.equal((await ask(Object.assign({}, base, { body_hex: toHex(body) }))).value, true);
	assert.equal((await ask(Object.assign({}, base, { body_hex: toHex(Buffer.from("{}")) }))).value, false);
	assert.equal((await ask(Object.assign({}, base, { body_hex: toHex(body), alg: "Ed25519" }))).value, false);
	assert.equal((await ask(Object.assign({}, base, { body_hex: toHex(body), gw: "GW-WEB-02" }))).value, false);
	// the private key cannot be exported
	await assert.rejects(crypto.subtle.exportKey("pkcs8", id.keyPair.privateKey));
});

// ------------------------------------------------------------------ station loop with a fake server
test("station: lease -> MCF -> result, then idle", async () => {
	const s = await newSim();
	const results = [];
	let leases = [makeOp("OP-S1", "SINV-S1"), makeOp("OP-S2", "SINV-S2")];
	const server = {
		heartbeat: async () => ({}), event: async () => ({}),
		lease: async () => leases.shift() || { idle: true },
		result: async (r) => { results.push(r); return leases.length ? { next: leases.shift() } : {}; },
	};
	const station = new M.Station({ server, journal: new M.MemoryJournal(), device: s.dev,
		config: { expected_nid: "SIM0000001", expected_nif: "A0700190Q" } });
	const delay = await station.runOnce();
	assert.equal(delay, 200);
	assert.deepEqual(results.map((r) => r.outcome), ["normalized", "normalized"]);
	assert.equal((await s.state()).invoices.length, 2);
	assert.ok(await station.runOnce() >= 2000);
});
