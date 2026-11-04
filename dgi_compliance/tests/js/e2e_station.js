/*
 * End-to-end test of the Poste MCF page in a real Chromium (Playwright):
 *   cashier login -> page Poste MCF -> "Connecter le MCF" (serial port = MCF simulator) -> enrolment
 *   with the code -> approval by another person (bench) -> invoices of an e-MCF point de vente while
 *   the e-MCF is unreachable -> automatic MCF fallback -> normalized by the browser.
 *
 *   node e2e_station.js <site_url> <enrolment_code>
 * Requires: bench serve on <site_url>, `bench execute dgi_compliance.tests.e2e_station.prepare` (gives the
 * code), PYTHON (bench env) and BENCH (path to frappe-bench) env vars. TEST SITES ONLY.
 *
 * Web Serial cannot reach a real port in a headless test: navigator.serial is replaced (before the
 * page loads) by a port whose bytes go to the MCF simulator of dgi_gateway through sim_bridge.py.
 * Everything else is the real page, the real dgi_mcf.js, WebCrypto, IndexedDB and HTTP.
 */
"use strict";
const path = require("node:path");
const { spawn, execFileSync } = require("node:child_process");
const readline = require("node:readline");
const { chromium } = require("playwright");

const [, , SITE, CODE] = process.argv;
const BENCH = process.env.BENCH || "/home/frappe/frappe-bench";
const CASHIER = "caisse.e2e@example.com", PASSWORD = "Caisse#E2E-2026!";

const py = spawn(process.env.PYTHON || "python3", [path.join(__dirname, "sim_bridge.py")], { stdio: ["pipe", "pipe", "inherit"] });
const rl = readline.createInterface({ input: py.stdout });
const waiting = [];
rl.on("line", (l) => waiting.shift()(JSON.parse(l)));
const ask = (req) => new Promise((r) => { waiting.push(r); py.stdin.write(JSON.stringify(req) + "\n"); });

function bench(method, kwargs) {
	const args = ["--site", process.env.SITE_NAME || "test.local", "execute", method];
	if (kwargs) args.push("--kwargs", JSON.stringify(kwargs));
	const out = execFileSync("su", ["-", "frappe", "-c", `[ -f ~/env.sh ] && . ~/env.sh; cd ${BENCH} && bench ${args.map((a) => `'${a}'`).join(" ")} 2>/dev/null`]).toString();
	return out.trim().split("\n").pop();
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const check = (cond, msg) => { if (!cond) throw new Error("ECHEC : " + msg); console.log("OK  " + msg); };

const FAKE_SERIAL = () => {
	class FakePort {
		getInfo() { return { usbVendorId: 0x1a86, usbProductId: 0x7523 }; }
		async open() {
			const self = this;
			this.readable = new ReadableStream({ start(c) { self.ctrl = c; } });
			this.writable = new WritableStream({
				async write(chunk) {
					const hex = Array.from(chunk, (b) => b.toString(16).padStart(2, "0")).join("");
					const out = await window.__mcfWrite(hex);
					if (out) self.ctrl.enqueue(Uint8Array.from(out.match(/../g).map((h) => parseInt(h, 16))));
				},
			});
		}
		async close() { try { this.ctrl.close(); } catch (e) { /* closed */ } }
	}
	const port = new FakePort();
	Object.defineProperty(navigator, "serial", { configurable: true, value: {
		requestPort: async () => port, getPorts: async () => [port],
		addEventListener() {}, removeEventListener() {},
	} });
};

(async () => {
	await ask({ op: "new", id: "web", nid: "WEB0000001", nif: "A0700190Q", rates: { A: 0, B: 16, C: 5, O: 1, P: 1 } });
	const browser = await chromium.launch({ args: [`--unsafely-treat-insecure-origin-as-secure=${SITE}`] });
	const page = await browser.newPage();
	page.on("pageerror", (e) => console.log("[page error]", e.message));
	page.on("console", (m) => { if (["error", "warning"].includes(m.type())) console.log("[console]", m.text().slice(0, 300)); });
	global.__page = page;
	await page.exposeFunction("__mcfWrite", async (hex) => (await ask({ op: "write", id: "web", hex })).hex || "");
	await page.addInitScript(FAKE_SERIAL);

	// 1. cashier login
	const login = await page.request.post(`${SITE}/api/method/login`, { data: { usr: CASHIER, pwd: PASSWORD } });
	if (login.status() !== 200) throw new Error("login " + login.status());
	await page.goto(`${SITE}/app`);
	check(true, "connexion de la caissiere (role DGI Caisse MCF)");

	// 2. Poste MCF page, station choice, MCF connection
	await page.goto(`${SITE}/app/dgi-mcf-station`);
	await page.waitForSelector(".dgi-st-gw", { timeout: 30000 });
	check(await page.evaluate(() => window.isSecureContext && !!crypto.subtle), "contexte securise (WebCrypto disponible)");
	await page.selectOption(".dgi-st-gw", "GW-WEB-E2E");
	await page.waitForSelector(".dgi-st-code", { timeout: 15000 });
	await page.click(".dgi-st-connect");
	await page.waitForSelector(".dgi-st-device table", { timeout: 15000 });
	const devText = await page.textContent(".dgi-st-device");
	check(devText.includes("WEB0000001-1"), "MCF connecte par le port serie (C1h lu par le navigateur)");

	// 3. enrolment with the code
	await page.fill(".dgi-st-code", CODE);
	await page.click(".dgi-st-enrol");
	await page.waitForFunction(() => /En attente d'approbation/.test(document.querySelector(".dgi-st-step").innerText), null, { timeout: 15000 });
	const fp = (await page.textContent(".dgi-st-step div")).trim().match(/[0-9A-F]{4}(-[0-9A-F]{4}){4}/)[0];
	check(fp.length === 24, `empreinte affichee au poste : ${fp}`);
	const exportable = await page.evaluate(async () => {
		const rec = await new DGIMCF.KeyStore().get("GW-WEB-E2E");
		try { await crypto.subtle.exportKey("pkcs8", rec.keyPair.privateKey); return true; } catch (e) { return false; }
	});
	check(!exportable, "cle privee du poste non exportable (WebCrypto, IndexedDB)");

	// 4. approval by another person (the manager compares the fingerprint)
	check(bench("dgi_compliance.tests.e2e_station.approve", { fingerprint: fp }).includes("APPROVED=Active"), "poste approuve par le responsable (autre personne)");
	await page.waitForFunction(() => /demarre/.test(document.querySelector(".dgi-st-log").innerText), null, { timeout: 30000 });
	check(true, "le poste demarre seul apres l'approbation (jeton operateur)");

	// 5. e-MCF unreachable -> automatic fallback -> normalized by the browser
	const invoices = [];
	for (let i = 0; i < 2; i++) {
		const out = bench("dgi_compliance.tests.e2e_station.new_invoice");
		check(/STAGE=queued-mcf/.test(out), `facture transmise au MCF de secours (${out})`);
		invoices.push(out.match(/INVOICE=(\S+)/)[1]);
	}
	const states = {};
	for (let t = 0; t < 40; t++) {
		for (const inv of invoices) states[inv] = JSON.parse(bench("dgi_compliance.tests.e2e_station.invoice_state", { name: inv }).replace(/^STATE=/, ""));
		if (invoices.every((i) => states[i].status === "Normalized")) break;
		await sleep(2000);
	}
	for (const inv of invoices) {
		const s = states[inv];
		check(s.status === "Normalized" && s.channel === "MCF" && /^WEB0000001-1$/.test(s.nim), `${inv} normalisee par le MCF via le navigateur (${s.code_def})`);
		check(s.operation.station_user === CASHIER && s.operation.fallback === 1, `${inv} : operateur ${s.operation.station_user}, secours trace`);
	}
	check(states[invoices[0]].fallback_since && states[invoices[0]].fallback_since !== "None", "secours MCF actif sur le point de vente (trace)");
	const sim = await ask({ op: "state", id: "web" });
	check(sim.invoices.length === 2, "le MCF a enregistre exactement 2 factures (aucun doublon)");

	// 6. signed calls without cookies: the cashier's session is untouched
	const me = await page.evaluate(async () => (await (await fetch("/api/method/frappe.auth.get_logged_user")).json()).message);
	check(me === CASHIER, "la session de la caissiere est intacte apres les appels signes");
	const replay = await page.evaluate(async () => {
		const r = await fetch("/api/method/dgi_compliance.gateway.api.lease", { method: "POST", credentials: "include",
			headers: { "Content-Type": "application/json", "X-Frappe-CSRF-Token": frappe.csrf_token }, body: "{}" });
		return r.status;
	});
	check(replay >= 400, `appel de passerelle avec la session et sans signature refuse (HTTP ${replay})`);
	await page.screenshot({ path: process.env.SHOT || "/tmp/poste_mcf.png", fullPage: true });
	await browser.close();
	py.kill();
	console.log("E2E POSTE MCF : TOUT EST OK");
})().catch(async (e) => {
	console.error(e);
	try {
		await global.__page.screenshot({ path: (process.env.SHOT || "/tmp/poste_mcf.png").replace(".png", "-echec.png"), fullPage: true });
		console.log("[page]", (await global.__page.innerText("body")).slice(0, 1500));
	} catch (x) { /* no page */ }
	py.kill(); process.exit(1);
});
